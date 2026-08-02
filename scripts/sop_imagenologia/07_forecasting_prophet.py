import logging
import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd
import numpy as np
from prophet import Prophet
import duckdb


# CONFIGURACIÓN Y PARÁMETROS DEL MODELO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

DIAS_PROYECCION_FUTURA = 90
INTERVALO_CONFIANZA = 0.85


# FUNCIONES DEL PIPELINE PREDICTIVO

def cargar_feriados(ruta_feriados: Path) -> list:
    """Carga la lista de feriados desde la primera columna del Excel externo."""
    if not ruta_feriados.exists():
        logging.warning(f"No se encontró el archivo {ruta_feriados.name}. Se asumirá sin feriados.")
        return []
    try:
        df_feriados = pd.read_excel(ruta_feriados, usecols=[0], names=['fecha'])
        fechas = pd.to_datetime(df_feriados['fecha'], format='%d-%m-%Y', errors='coerce').dropna()
        feriados_lista = [d.date() for d in fechas]
        logging.info(f"Feriados cargados exitosamente: {len(feriados_lista)} fechas detectadas.")
        return feriados_lista
    except Exception as e:
        logging.error(f"Error al procesar {ruta_feriados.name}: {e}")
        return []

def cargar_demanda_historica(ruta_parquet: Path) -> pd.DataFrame:
    """Extrae la demanda histórica diaria del datalake operacional."""
    if not ruta_parquet.exists():
        logging.error(f"No se encuentra {ruta_parquet.name}. Ejecuta el pipeline ETL.")
        return pd.DataFrame()

    df = pd.read_parquet(ruta_parquet)
    demanda_diaria = (
        df.groupby('Fecha')
        .size()
        .reset_index(name='y')
        .rename(columns={'Fecha': 'ds'})
    )
    demanda_diaria['ds'] = pd.to_datetime(demanda_diaria['ds'])
    
    logging.info(f"Demanda histórica cargada: {len(demanda_diaria)} días operativos.")
    return demanda_diaria

def entrenar_modelo_prophet(demanda_diaria: pd.DataFrame, feriados_cl: list) -> Prophet:
    """Entrena el modelo de Machine Learning considerando estacionalidad y feriados reales."""
    logging.info("Entrenando modelo Prophet (Meta/Facebook) con datos históricos...")
    
    df_feriados = pd.DataFrame({
        'holiday': 'feriado_chile',
        'ds': pd.to_datetime(feriados_cl),
        'lower_window': 0,
        'upper_window': 1,
    }) if feriados_cl else None

    modelo = Prophet(
        holidays=df_feriados,
        yearly_seasonality=True,    
        weekly_seasonality=True,    
        daily_seasonality=False,    
        seasonality_mode='multiplicative', 
        interval_width=INTERVALO_CONFIANZA          
    )
    
    modelo.fit(demanda_diaria)
    return modelo

def proyectar_futuro(modelo: Prophet, demanda_diaria: pd.DataFrame, dias: int = DIAS_PROYECCION_FUTURA) -> pd.DataFrame:
    """Genera la proyección diaria hacia el futuro."""
    logging.info(f"Generando proyección predictiva para los próximos {dias} días...")
    futuro = modelo.make_future_dataframe(periods=dias)
    forecast = modelo.predict(futuro)
    
    # El "hoy" lógico es el último día con datos históricos reales
    ultimo_dia_historia = demanda_diaria['ds'].max()
    
    # Filtrar estrictamente hacia el futuro respecto a nuestra base de datos
    return forecast[forecast['ds'] > ultimo_dia_historia].copy()

def integrar_capacidad_operativa(forecast: pd.DataFrame, ruta_cuotas: Path, feriados_cl: list) -> pd.DataFrame:
    """Integra el pronóstico de demanda con la capacidad real del equipo médico."""
    if ruta_cuotas.exists():
        df_cuotas = pd.read_parquet(ruta_cuotas)
        cap_por_dia_semana = df_cuotas.groupby('dia_asignacion')['cuota_entrada'].sum().to_dict()
    else:
        logging.warning("matriz_cuotas_integrada no encontrada. Asumiendo capacidad 0.")
        cap_por_dia_semana = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    def capacidad_de_fecha(fecha_ts: pd.Timestamp) -> float:
        d = fecha_ts.date()
        # Filtro duro: Fines de semana y Feriados leídos del Excel = 0 capacidad
        if d.weekday() >= 5 or d in feriados_cl: return 0.0
        return cap_por_dia_semana.get(d.weekday() + 1, 0.0)

    forecast['capacidad_diaria'] = forecast['ds'].apply(capacidad_de_fecha)
    forecast['semana_str'] = (
        forecast['ds'].dt.isocalendar().year.astype(str) + "-W" + 
        forecast['ds'].dt.isocalendar().week.astype(str).str.zfill(2)
    )
    return forecast

def agregar_metricas_semanales(forecast: pd.DataFrame, ruta_factor: Path) -> pd.DataFrame:
    """Agrega la demanda a nivel semanal de forma segura para Pandas 3.x."""
    logging.info("Agregando métricas a resolución semanal (Control S&OP)...")
    
    # Asegurar numéricos para evitar que Prophet inyecte objetos raros
    cols_numericas = ['yhat', 'yhat_lower', 'yhat_upper', 'capacidad_diaria']
    for col in cols_numericas:
        forecast[col] = pd.to_numeric(forecast[col], errors='coerce').fillna(0)

    # 1. Iteración manual por semana para ser inmunes a los bugs de agrupación
    grupos = forecast.groupby('semana_str')
    filas_semanales = []
    
    for semana, df_grupo in grupos:
        filas_semanales.append({
            'Semana': semana,
            'fecha_inicio': df_grupo['ds'].min(),
            'fecha_fin': df_grupo['ds'].max(),
            'dias_habiles_semana': int((df_grupo['capacidad_diaria'] > 0).sum()),
            'examenes_esperados': df_grupo['yhat'].sum(),
            'escenario_optimista': df_grupo['yhat_lower'].sum(),
            'escenario_pesimista': df_grupo['yhat_upper'].sum(),
            'capacidad_interna': df_grupo['capacidad_diaria'].sum()
        })
        
    forecast_semanal = pd.DataFrame(filas_semanales)

    # 2. Redondeos y métricas secundarias
    cols_num = ['examenes_esperados', 'escenario_optimista', 'escenario_pesimista', 'capacidad_interna']
    forecast_semanal[cols_num] = forecast_semanal[cols_num].round(0)

    forecast_semanal['derivacion_itms_esperada'] = (forecast_semanal['examenes_esperados'] - forecast_semanal['capacidad_interna']).clip(lower=0).round(0)
    forecast_semanal['alerta_semana_corta'] = forecast_semanal['dias_habiles_semana'].apply(lambda n: f"⚠️ {n} días hábiles" if n < 5 else "✅ Semana completa")
    forecast_semanal['tasa_ocupacion_interna'] = (forecast_semanal['capacidad_interna'] / forecast_semanal['examenes_esperados'].clip(lower=1)).clip(upper=1.0).round(3)

    # 3. Traducción Financiera (Factor MK)
    factor_actual = 1.358
    if ruta_factor.exists():
        df_factor = pd.read_parquet(ruta_factor)
        factor_actual = float(df_factor[df_factor['Modalidad'] == 'GLOBAL']['Factor_Agrupacion'].values[0])

    forecast_semanal['prestaciones_mk_equivalentes'] = (forecast_semanal['examenes_esperados'] * factor_actual).round(0)
    forecast_semanal['brecha_itms_prestaciones'] = (forecast_semanal['derivacion_itms_esperada'] * factor_actual).round(0)

    return forecast_semanal


# ORQUESTADOR PRINCIPAL

def orquestar_forecasting_prophet(dir_datos: Path, dir_salidas: Path):
    logging.info("INICIANDO MOTOR DE MACHINE LEARNING ")

    # 1. Rutas
    ruta_fact = dir_salidas / 'fact_examenes.parquet'
    ruta_cuotas = dir_salidas / 'matriz_cuotas_integrada.parquet'
    ruta_factor = dir_salidas / 'factor_agrupacion.parquet'
    ruta_feriados = dir_datos / 'externos/feriados.xlsx'

    # 2. Ingesta de Feriados y Demanda
    feriados_cl = cargar_feriados(ruta_feriados)
    demanda_diaria = cargar_demanda_historica(ruta_fact)
    
    if demanda_diaria.empty: return

    # 3. Entrenamiento
    modelo = entrenar_modelo_prophet(demanda_diaria, feriados_cl)
    
    # 4. Predicción e Integración de Capacidad (Pasando la demanda para calcular el "hoy")
    forecast = proyectar_futuro(modelo, demanda_diaria)
    forecast_integrado = integrar_capacidad_operativa(forecast, ruta_cuotas, feriados_cl)
    
    # 5. Cálculo Semanal S&OP
    forecast_semanal = agregar_metricas_semanales(forecast_integrado, ruta_factor)

    # 6. Exportaciones
    dir_salidas.mkdir(parents=True, exist_ok=True)
    
    columnas_gerencia = [
        'Semana', 'fecha_inicio', 'fecha_fin', 'dias_habiles_semana',
        'examenes_esperados', 'prestaciones_mk_equivalentes', 
        'capacidad_interna', 'derivacion_itms_esperada', 'brecha_itms_prestaciones',
        'tasa_ocupacion_interna', 'alerta_semana_corta'
    ]

    # Excel
    ruta_forecast_xl = dir_salidas / 'Forecast_Proximas_8_Semanas.xlsx'
    forecast_semanal[columnas_gerencia].to_excel(ruta_forecast_xl, index=False)

    # Parquet (Sanitizado para Power BI)
    df_pbi = forecast_semanal[columnas_gerencia].copy()
    df_pbi['fecha_inicio'] = df_pbi['fecha_inicio'].dt.strftime('%Y-%m-%d')
    df_pbi['fecha_fin'] = df_pbi['fecha_fin'].dt.strftime('%Y-%m-%d')
    for col in df_pbi.select_dtypes(include=["object","str"]).columns:
        df_pbi[col] = df_pbi[col].astype(str).str.strip()

    ruta_forecast_pq = dir_salidas / 'forecast_semanal_prophet.parquet'
    df_pbi.to_parquet(ruta_forecast_pq, index=False)

    # 7. Reporte en Consola
    logging.info(f"Forecast táctico exportado a: {ruta_forecast_xl.name}")
    logging.info("REPORTE DE GESTIÓN TÁCTICA (PRÓXIMAS 8 SEMANAS) ")
    
    print(f"\n{'─'*85}")
    print(f"{'Semana':<12} {'Demanda':>9} {'Cap.Interna':>12} {'ITMS':>8} {'Ocupación':>10} {'Estado'}")
    print(f"{'─'*85}")
    for _, row in forecast_semanal.head(8).iterrows():
        print(
            f"{row['Semana']:<12} "
            f"{row['examenes_esperados']:>9.0f} "
            f"{row['capacidad_interna']:>12.0f} "
            f"{row['derivacion_itms_esperada']:>8.0f} "
            f"{row['tasa_ocupacion_interna']:>9.1%} "
            f"  {row['alerta_semana_corta']}"
        )
    print(f"{'─'*85}")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    orquestar_forecasting_prophet(
        dir_datos=BASE_DIR / 'data/sop_imagenologia',
        dir_salidas=BASE_DIR / 'output/sop_imagenologia'
    )