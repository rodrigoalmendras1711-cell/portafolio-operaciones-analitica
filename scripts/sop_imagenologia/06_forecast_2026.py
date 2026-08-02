import logging
import datetime
import calendar
import os
from itertools import product
from pathlib import Path
from typing import Dict, Tuple, List

import pandas as pd
import numpy as np
import duckdb


# CONFIGURACIÓN Y PARÁMETROS DEL MODELO
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

ANIO_OBJETIVO = 2026
BUFFER_PESIMISTA = 1.30
ALPHA_DECAIMIENTO = 0.85

NOMBRES_MES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto", 
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre"
}


# FUNCIONES CORE: FORECASTING Y MEMORIA AUTORREGRESIVA

def cargar_feriados(ruta_feriados: Path) -> list:
    """Carga la lista de feriados """
    if not ruta_feriados.exists():
        logging.warning(f"No se encontró el archivo {ruta_feriados.name}. Se asumirá sin feriados.")
        return []
    try:
        df_feriados = pd.read_excel(ruta_feriados, usecols=[0], names=['fecha'])
        fechas = pd.to_datetime(df_feriados['fecha'], format='%d-%m-%Y', errors='coerce').dropna()
        feriados_lista = [d.date() for d in fechas]
        logging.info(f"Catálogo de feriados cargado: {len(feriados_lista)} fechas detectadas.")
        return feriados_lista
    except Exception as e:
        logging.error(f"Error al procesar {ruta_feriados.name}: {e}")
        return []

def dias_habiles_mes(anio: int, mes: int, feriados_cl: list) -> int:
    """Calcula los días operativos reales del mes descontando fines de semana y feriados."""
    n = 0
    for d in range(1, calendar.monthrange(anio, mes)[1] + 1):
        fecha = datetime.date(anio, mes, d)
        if fecha.weekday() < 5 and fecha not in feriados_cl:
            n += 1
    return n

def calcular_demanda_ensemble(df_vol: pd.DataFrame, anio_obj: int, mes_obj: int, w_est: float, w_tend: float, forecast_acumulado: Dict = None) -> Tuple[int, int, int]:
    """Calcula la demanda usando un modelo Ensemble (Tendencia YTD + Estacionalidad)."""
    if forecast_acumulado is None: forecast_acumulado = {}

    #  1. Componente de Tendencia (YTD) 
    vol_mismo_mes_pasado = df_vol[(df_vol["anio"] == anio_obj - 1) & (df_vol["mes"] == mes_obj)]["volumen_total"].sum()

    if mes_obj > 1:
        meses_reales = df_vol[(df_vol["anio"] == anio_obj) & (df_vol["mes"] < mes_obj)]["mes"].tolist()
        if meses_reales:
            ytd_act = df_vol[(df_vol["anio"] == anio_obj) & (df_vol["mes"].isin(meses_reales))]["volumen_total"].sum()
            ytd_ant = df_vol[(df_vol["anio"] == anio_obj - 1) & (df_vol["mes"].isin(meses_reales))]["volumen_total"].sum()
            f_crec = ytd_act / ytd_ant if ytd_ant > 0 else 1.0
        else:
            v1 = df_vol[df_vol["anio"] == anio_obj - 1]["volumen_total"].sum()
            v2 = df_vol[df_vol["anio"] == anio_obj - 2]["volumen_total"].sum()
            f_crec = v1 / v2 if v2 > 0 else 1.0
    else:
        v1 = df_vol[df_vol["anio"] == anio_obj - 1]["volumen_total"].sum()
        v2 = df_vol[df_vol["anio"] == anio_obj - 2]["volumen_total"].sum()
        f_crec = v1 / v2 if v2 > 0 else 1.0

    dem_tend = int(vol_mismo_mes_pasado * f_crec)

    # 2. Componente Estacional (Walk-Forward) 
    m_ant = mes_obj - 1 if mes_obj > 1 else 12
    a_ant = anio_obj if mes_obj > 1 else anio_obj - 1
    ratios = []
    
    for a_h in range(anio_obj - 3, anio_obj):
        a_h_ant = a_h if mes_obj > 1 else a_h - 1
        v_ant = df_vol[(df_vol["anio"] == a_h_ant) & (df_vol["mes"] == m_ant)]["volumen_total"].sum()
        v_mes = df_vol[(df_vol["anio"] == a_h) & (df_vol["mes"] == mes_obj)]["volumen_total"].sum()
        if v_ant > 0 and v_mes > 0:
            ratios.append(v_mes / v_ant)
            
    ratio_prom = float(np.mean(ratios)) if ratios else 1.0

    v_base = df_vol[(df_vol["anio"] == a_ant) & (df_vol["mes"] == m_ant)]["volumen_total"].sum()
    if v_base == 0:
        v_base = forecast_acumulado.get((a_ant, m_ant), 0)

    dem_est = int(v_base * ratio_prom)
    dem_base = int((w_est * dem_est) + (w_tend * dem_tend))
    
    return dem_est, dem_tend, dem_base

def grid_search_pesos(df_vol: pd.DataFrame, anio_obj: int) -> Tuple[float, float, float]:
    """Optimización de hiperparámetros minimizando el Error Absoluto Medio (MAE)."""
    pesos = [round(x * 0.1, 1) for x in range(1, 10)]
    menor_mae = float("inf")
    mejores = (0.6, 0.4)
    meses_val = df_vol[df_vol["anio"] == anio_obj - 1].sort_values("mes")

    for w_est, w_tend in product(pesos, repeat=2):
        if abs(w_est + w_tend - 1.0) > 0.011: continue
        errores = []
        for _, row in meses_val.iterrows():
            _, _, pred = calcular_demanda_ensemble(df_vol, int(row["anio"]), int(row["mes"]), w_est, w_tend, {})
            errores.append(abs(pred - row["volumen_total"]))
        
        mae = float(np.mean(errores))
        if mae < menor_mae:
            menor_mae, mejores = mae, (w_est, w_tend)

    return mejores[0], mejores[1], menor_mae


# ORQUESTADOR PRINCIPAL DE LA TORRE DE CONTROL (S&OP)

def orquestar_forecast_anual(dir_datos: Path, dir_salidas: Path):
    logging.info(f"=== FORECAST ANUAL {ANIO_OBJETIVO} — CONTROL TOWER S&OP ===")

    ruta_parquet = dir_salidas / "fact_examenes.parquet"
    ruta_feriados = dir_datos / "externos/feriados.xlsx"

    if not ruta_parquet.exists():
        logging.error("No se encuentra fact_examenes.parquet. Ejecute ETL previo.")
        return

    feriados_cl = cargar_feriados(ruta_feriados)

    # 1. Extracción de Datos Históricos
    con = duckdb.connect()
    con.execute(f"CREATE VIEW fact AS SELECT * FROM parquet_scan('{ruta_parquet}')")

    df_hist = con.execute("""
        SELECT CAST(SUBSTRING(Anio_Mes, 1, 4) AS INT) AS anio,
               CAST(SUBSTRING(Anio_Mes, 6, 2) AS INT) AS mes,
               Anio_Mes, COUNT(*) AS volumen_total
        FROM fact GROUP BY 1, 2, 3 ORDER BY 1, 2
    """).df()

    df_hist_mod = con.execute("""
        SELECT CAST(SUBSTRING(Anio_Mes, 1, 4) AS INT) AS anio,
               CAST(SUBSTRING(Anio_Mes, 6, 2) AS INT) AS mes,
               Anio_Mes, Mod AS modalidad, COUNT(*) AS volumen
        FROM fact GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 4
    """).df()

    # Filtro de madurez de datos (Mínimo 12 meses de historia por modalidad)
    mods_validas = df_hist_mod.groupby("modalidad")["Anio_Mes"].nunique().loc[lambda x: x >= 12].index.tolist()
    df_hist_mod = df_hist_mod[df_hist_mod["modalidad"].isin(mods_validas)]
    logging.info(f"Modalidades operativas validadas: {sorted(mods_validas)}")

    # 2. Grid Search Global
    logging.info("Ejecutando Grid Search Walk-Forward...")
    w_est, w_tend, mae_global = grid_search_pesos(df_hist, ANIO_OBJETIVO)
    logging.info(f"Pesos óptimos -> Estacional: {w_est:.1f} | Tendencia: {w_tend:.1f} | MAE ±{mae_global:.0f} informes/mes")

    # 3. Forecast Mensual Total
    logging.info("Generando proyecciones encadenadas mensuales...")
    forecast_acumulado = {}
    filas_mensuales = []

    for mes in range(1, 13):
        dem_est, dem_tend, dem_base = calcular_demanda_ensemble(df_hist, ANIO_OBJETIVO, mes, w_est, w_tend, forecast_acumulado)
        forecast_acumulado[(ANIO_OBJETIVO, mes)] = dem_base 

        dh = dias_habiles_mes(ANIO_OBJETIVO, mes, feriados_cl)
        real_2025 = df_hist[(df_hist["anio"]==2025) & (df_hist["mes"]==mes)]["volumen_total"].sum()

        filas_mensuales.append({
            "Mes": NOMBRES_MES[mes], "N_Mes": mes, "Dias_Habiles": dh,
            "Real_2025": int(real_2025), "Dem_Estacional": dem_est,
            "Dem_Tendencia": dem_tend, "Demanda_Base": dem_base,
            "Demanda_Pesimista": int(dem_base * BUFFER_PESIMISTA),
            "Promedio_Diario_Base": round(dem_base/dh, 1) if dh > 0 else 0,
            "Factor_vs_2025": round(dem_base/real_2025, 3) if real_2025 > 0 else None,
        })

    df_mensual = pd.DataFrame(filas_mensuales)
    total_row = {
        "Mes": "TOTAL AÑO", "N_Mes": 99,
        "Dias_Habiles": df_mensual["Dias_Habiles"].sum(),
        "Real_2025": df_mensual["Real_2025"].sum(),
        "Dem_Estacional": df_mensual["Dem_Estacional"].sum(),
        "Dem_Tendencia": df_mensual["Dem_Tendencia"].sum(),
        "Demanda_Base": df_mensual["Demanda_Base"].sum(),
        "Demanda_Pesimista": df_mensual["Demanda_Pesimista"].sum(),
        "Promedio_Diario_Base": round(df_mensual["Demanda_Base"].sum()/df_mensual["Dias_Habiles"].sum(), 1),
        "Factor_vs_2025": round(df_mensual["Demanda_Base"].sum()/df_mensual["Real_2025"].sum(), 3),
    }
    df_mensual = pd.concat([df_mensual, pd.DataFrame([total_row])], ignore_index=True)

    # 4. Forecast por Modalidad
    hoy = datetime.date.today()
    df_mix = df_hist_mod.merge(df_hist[["anio","mes","volumen_total"]], on=["anio","mes"])
    df_mix["pct_mix"] = df_mix["volumen"] / df_mix["volumen_total"]

    filas_mod = []
    for mod in mods_validas:
        dm = df_mix[df_mix["modalidad"]==mod].copy()
        dm["peso"] = dm.apply(lambda r: ALPHA_DECAIMIENTO ** max((hoy.year-int(r["anio"]))*12+(hoy.month-int(r["mes"])), 0), axis=1)
        pct_pond = (dm["pct_mix"]*dm["peso"]).sum()/dm["peso"].sum() if dm["peso"].sum()>0 else 0
        v25, v24 = dm[dm["anio"]==2025]["volumen"].sum(), dm[dm["anio"]==2024]["volumen"].sum()
        
        filas_mod.append({
            "Modalidad": mod, "Vol_2024": int(v24), "Vol_2025": int(v25),
            "Tendencia_Anual": round(v25/v24 if v24>0 else 1.0, 3), 
            "Mix_Ponderado": round(pct_pond, 4)
        })

    df_modalidad_base = pd.DataFrame(filas_mod).sort_values("Vol_2025", ascending=False)
    dem_anual_total = df_mensual[df_mensual["N_Mes"]!=99]["Demanda_Base"].sum()
    df_modalidad_base["Demanda_2026_Base"] = (df_modalidad_base["Mix_Ponderado"] * dem_anual_total).round(0).astype(int)
    df_modalidad_base["Demanda_2026_Pesimista"] = (df_modalidad_base["Demanda_2026_Base"] * BUFFER_PESIMISTA).round(0).astype(int)
    df_modalidad_base["Mix_Pct"] = (df_modalidad_base["Mix_Ponderado"]*100).round(1)

    diff = dem_anual_total - df_modalidad_base["Demanda_2026_Base"].sum()
    if diff != 0: df_modalidad_base.iloc[0, df_modalidad_base.columns.get_loc("Demanda_2026_Base")] += int(diff)

    # 5. Forecast Cruzado (Mes x Modalidad)
    filas_cruce = []
    for mes in range(1, 13):
        dem_mes = df_mensual[df_mensual["N_Mes"]==mes]["Demanda_Base"].values[0]
        dh = df_mensual[df_mensual["N_Mes"]==mes]["Dias_Habiles"].values[0]
        for _, rm in df_modalidad_base.iterrows():
            mod = rm["Modalidad"]
            vol_mes_h = df_hist_mod[(df_hist_mod["modalidad"]==mod)&(df_hist_mod["mes"]==mes)]["volumen"].mean()
            vol_prom_h = df_hist_mod[df_hist_mod["modalidad"]==mod]["volumen"].mean()
            factor_est = vol_mes_h/vol_prom_h if pd.notna(vol_prom_h) and vol_prom_h > 0 else 1.0
            
            base = int(dem_mes * rm["Mix_Ponderado"] * factor_est)
            filas_cruce.append({
                "Mes": NOMBRES_MES[mes], "N_Mes": mes, "Modalidad": mod,
                "Dias_Habiles": int(dh), "Demanda_Base": base,
                "Demanda_Pesimista": int(base*BUFFER_PESIMISTA),
                "Promedio_Diario": round(base/dh, 1) if dh > 0 else 0,
            })

    df_cruce = pd.DataFrame(filas_cruce)
    for mes in range(1, 13):
        dem_real = df_mensual[df_mensual["N_Mes"]==mes]["Demanda_Base"].values[0]
        dem_cruce = df_cruce[df_cruce["N_Mes"]==mes]["Demanda_Base"].sum()
        if dem_cruce > 0:
            df_cruce.loc[df_cruce["N_Mes"]==mes, "Demanda_Base"] = (df_cruce.loc[df_cruce["N_Mes"]==mes, "Demanda_Base"] * (dem_real / dem_cruce)).round(0).astype(int)

    # 6. Saturación de Capacidad (Rho)
    ruta_cuotas = dir_salidas / "matriz_cuotas_integrada.parquet"
    df_rho = df_modalidad_base[["Modalidad", "Demanda_2026_Base", "Vol_2025", "Tendencia_Anual"]].copy()

    if ruta_cuotas.exists():
        df_cuotas = pd.read_parquet(ruta_cuotas)
        cap_x_mod = df_cuotas.groupby("modalidad")["capacidad_mensual"].sum().reset_index().rename(columns={"modalidad": "Modalidad", "capacidad_mensual": "Cap_Mensual_Prom"})
        df_rho = df_rho.merge(cap_x_mod, on="Modalidad", how="left")
        df_rho["Cap_Anual_Interna"] = df_rho["Cap_Mensual_Prom"] * 12
    else:
        df_rho["Cap_Anual_Interna"] = df_rho["Mix_Ponderado"] * (3173 * 12)

    df_rho["Demanda_Mensual_Prom"] = (df_rho["Demanda_2026_Base"] / 12).round(0)
    df_rho["rho_anual"] = (df_rho["Demanda_2026_Base"] / df_rho["Cap_Anual_Interna"]).round(3)
    
    df_rho["Diagnostico_Saturacion"] = df_rho["rho_anual"].apply(
        lambda r: "COLAPSO (ρ>1.0)" if pd.notna(r) and r > 1.0 else
                  "CRÍTICO (ρ 0.85-1.0)" if pd.notna(r) and r > 0.85 else
                  "ALTO (ρ 0.70-0.85)" if pd.notna(r) and r > 0.70 else
                  "ESTABLE (ρ<0.70)" if pd.notna(r) else "— Sin datos"
    )
    df_rho["Brecha_ITMS_Anual"] = (df_rho["Demanda_2026_Base"] - df_rho["Cap_Anual_Interna"]).clip(lower=0).round(0)

    # 7. Backlog Heredado
    df_backlog_raw = con.execute("""
        SELECT Anio_Mes AS mes_toma,
               CAST(SUBSTRING(CAST("Fecha de firma final" AS VARCHAR), 1, 7) AS VARCHAR) AS mes_firma,
               COUNT(*) AS examenes
        FROM fact
        WHERE "Fecha de firma final" IS NOT NULL AND Tipo_Firmante = 'INTERNO'
        GROUP BY 1, 2 ORDER BY 1, 2
    """).df()

    df_backlog_raw["es_backlog"] = df_backlog_raw["mes_firma"] > df_backlog_raw["mes_toma"]
    df_backlog = df_backlog_raw[df_backlog_raw["es_backlog"]].groupby("mes_toma")["examenes"].sum().reset_index().rename(columns={"mes_toma": "Anio_Mes", "examenes": "Backlog_Generado"})

    mes_toma_dt = pd.to_datetime(df_backlog_raw["mes_toma"] + "-01")
    mes_firma_dt = pd.to_datetime(df_backlog_raw["mes_firma"] + "-01")
    df_backlog_raw["es_mes_siguiente"] = mes_firma_dt == (mes_toma_dt + pd.DateOffset(months=1))

    df_heredado = df_backlog_raw[df_backlog_raw["es_mes_siguiente"]].groupby("mes_toma")["examenes"].sum().reset_index().rename(columns={"mes_toma": "Anio_Mes", "examenes": "Backlog_Heredado"})
    df_backlog_final = df_backlog.merge(df_heredado, on="Anio_Mes", how="left").merge(df_hist[["Anio_Mes", "volumen_total"]], on="Anio_Mes", how="left")
    df_backlog_final["Pct_Backlog_vs_Demanda"] = (df_backlog_final["Backlog_Generado"] / df_backlog_final["volumen_total"]).round(3)
    
    con.close()

    # 8. Factores Estacionales Observados
    filas_estacional = []
    for mes in range(1, 13):
        vol_mes_lista = [v for v in df_hist[df_hist["mes"] == mes]["volumen_total"].tolist() if v > 0]
        prom_anual_lista = [df_hist[df_hist["anio"] == a]["volumen_total"].mean() for a in df_hist[df_hist["mes"] == mes]["anio"].unique()]
        factores = [v/p for v, p in zip(vol_mes_lista, prom_anual_lista) if p > 0]
        
        r23 = df_hist[(df_hist["anio"] == 2023) & (df_hist["mes"] == mes)]["volumen_total"].sum()
        r24 = df_hist[(df_hist["anio"] == 2024) & (df_hist["mes"] == mes)]["volumen_total"].sum()
        r25 = df_hist[(df_hist["anio"] == 2025) & (df_hist["mes"] == mes)]["volumen_total"].sum()
        
        filas_estacional.append({
            "Mes": NOMBRES_MES[mes], "N_Mes": mes,
            "Real_2023": int(r23), "Real_2024": int(r24), "Real_2025": int(r25),
            "Forecast_2026": df_mensual[df_mensual["N_Mes"]==mes]["Demanda_Base"].values[0],
            "Factor_Estacional_Prom": round(float(np.mean(factores)), 3) if factores else None,
            "Interpretacion": "🏔️ Pico" if factores and np.mean(factores) > 1.10 else "🌊 Valle" if factores and np.mean(factores) < 0.90 else "📊 Normal"
        })
    df_estacional = pd.DataFrame(filas_estacional)

    # 9. Pivot Table
    df_pivot_cruce = df_cruce.pivot_table(index="Modalidad", columns="Mes", values="Demanda_Base", aggfunc="sum").reindex(columns=list(NOMBRES_MES.values()), fill_value=0)
    df_pivot_cruce["TOTAL_AÑO"] = df_pivot_cruce.sum(axis=1)
    df_pivot_cruce.loc["TOTAL_MES"] = df_pivot_cruce.sum(axis=0)
    df_pivot_cruce = df_pivot_cruce.reset_index()

    # 10. Exportaciones
    dir_salidas.mkdir(parents=True, exist_ok=True)
    
    ruta_out_excel = dir_salidas / "Forecast_Anual_2026.xlsx"
    with pd.ExcelWriter(ruta_out_excel, engine="openpyxl") as writer:
        df_mensual.to_excel(writer, sheet_name="1_Mensual_Total", index=False)
        df_modalidad_base.to_excel(writer, sheet_name="2_Por_Modalidad", index=False)
        df_pivot_cruce.to_excel(writer, sheet_name="3_Pivot_Modal_x_Mes", index=False)
        df_cruce.sort_values(["N_Mes", "Modalidad"]).to_excel(writer, sheet_name="4_Detalle_Mes_x_Modal", index=False)
        df_rho.to_excel(writer, sheet_name="5_Saturacion_x_Modal", index=False)
        df_backlog_final.to_excel(writer, sheet_name="6_Backlog_Heredado", index=False)
        df_estacional.to_excel(writer, sheet_name="7_Factores_Estacionales", index=False)

    df_pbi_anual = df_cruce.copy()
    df_pbi_anual['Anio_Mes'] = str(ANIO_OBJETIVO) + "-" + df_pbi_anual['N_Mes'].astype(str).str.zfill(2)
    for col in df_pbi_anual.select_dtypes(include=["object","str"]).columns:
        df_pbi_anual[col] = df_pbi_anual[col].astype(str).str.strip()
    
    df_pbi_anual.to_parquet(dir_salidas / "forecast_anual_2026.parquet", index=False)

    # 11. Reporte Ejecutivo en Consola
    tot = df_mensual[df_mensual["N_Mes"]==99].iloc[0]
    logging.info(" RESULTADOS FORECAST 2026 ")
    logging.info(f"Real 2025               : {tot['Real_2025']:>8,} exámenes")
    logging.info(f"Forecast Base 2026      : {tot['Demanda_Base']:>8,} exámenes ({(tot['Demanda_Base']/tot['Real_2025']-1)*100:+.1f}%)")
    logging.info(f"Forecast Pesimista 2026 : {tot['Demanda_Pesimista']:>8,} exámenes")
    logging.info(f"MAE de Validación       : ±{mae_global:.0f}")
    logging.info(f"Archivos exportados a   : {dir_salidas.name}/")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    orquestar_forecast_anual(
        dir_datos=BASE_DIR / 'data/sop_imagenologia',
        dir_salidas=BASE_DIR / 'output/sop_imagenologia'
    )