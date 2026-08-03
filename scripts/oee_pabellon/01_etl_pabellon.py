"""

ETL PABELLÓN QUIRÚRGICO  |  Clínica

Proyecto: Optimización de Capacidad Estocástica de Pabellón
Autor: Rodrigo Almendras (Ingeniería Civil Industrial)

PIPELINE:
  1. Ingesta multi-mensual (Nov/Dic/Ene/Feb) → Concatenación
  2. Limpieza: Tiempos, prefijos, normalización
  3. Cálculo de 8 métricas temporales derivadas
  4. Newsvendor: Percentiles críticos (P50/P70/P85/P90) por intervención y cirujano
  5. SPC: Límites de control (UCL/LCL) por cirujano (X-barra y Reglas de Nelson)
  6. OEE Quirúrgico: Eficiencia global por pabellón y día
  7. Output: Archivos procesados listos para ingesta en Power BI

"""

import os
import re
import unicodedata
import pandas as pd
import numpy as np
from pathlib import Path
from normalizador_fuzzy import normalizar_intervencion


# 0. CONFIGURACIÓN DE RUTAS Y PARÁMETROS BASE

# Definición de rutas relativas al proyecto
BASE_DIR = Path(__file__).resolve().parent.parent.parent

INPUT_DIR = BASE_DIR / "data" / "oee_pabellon"
OUTPUT_DIR = BASE_DIR / "output" / "oee_pabellon"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Parámetros operativos
HORAS_DISPONIBLES_MIN = 960  # 07:00 a 23:00 = 960 min
HOLGURA_MIN = 15             # Tolerancia para considerar "En Hora"

TIME_COLS = [
    "montaje", "fin mont", "ing pac", "ini anes", "ing med",
    "ini cx", "fin cx", "sal pac", "fin orden", "ini aseo", "fin aseo",
    "Fin anes"
]



# 1. HELPERS: PROCESAMIENTO DE TIEMPO

def to_minutes(val) -> float:
    """Convierte formatos de tiempo 'HH:MM:SS' a minutos (float)"""
    if pd.isna(val):
        return np.nan
    if isinstance(val, pd.Timedelta):
        return val.total_seconds() / 60
    
    s = str(val).strip()
    parts = s.split(":")
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except Exception:
        return np.nan


def duration(df: pd.DataFrame, start_col: str, end_col: str) -> pd.Series:
    """Calcula la duración en minutos entre dos columnas de tiempo, corrigiendo el cruce de medianoche."""
    s = df[start_col].map(to_minutes)
    e = df[end_col].map(to_minutes)
    diff = e - s
    diff = diff.where(diff >= -60, diff + 1440)  # Corrección cruce medianoche
    diff = diff.where(diff >= 0, np.nan)         # Omite valores imposibles negativos
    return diff



# NORMALIZACIÓN Y CLASIFICACIÓN DE INTERVENCIONES

def quitar_tildes(s: str) -> str:
    """Remueve tildes y caracteres de un string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def clasificar_especialidad(intervencion: str) -> str:
    """Clasifica una intervención quirúrgica en su especialidad médica correspondiente."""
    i = quitar_tildes(str(intervencion)).upper()
    
    if any(x in i for x in ["BYPASS", "MANGA GASTRICA", "BARIATRIC"]):
        return "Bariátrica"
    if any(x in i for x in ["COLECISTECTOMIA", "APENDICECTOMIA", "HERNIA", "COLON"]):
        return "Cirugía General"
    if any(x in i for x in ["RINOPLASTIA", "RINOSEPTOPLASTIA", "AMIGDAL", "ADENOID", "TIMPANO", "SEPTUM", "SENOS PARANASALES"]):
        return "ORL / Cabeza y Cuello"
    if any(x in i for x in ["ARTROSCOPIA", "PROTESIS", "PTC", "PTR", "FRACTURA", "OSTEOSINTESIS", "MENISCECTOMIA", "SAFENECTOMIA"]):
        return "Traumatología / Ortopedia"
    if any(x in i for x in ["ABDOMINOPLASTIA", "MAMOPLASTIA", "LIPOSUCCION", "BLEPARO", "RINOPLASTIA"]):
        return "Cirugía Plástica"
    if any(x in i for x in ["HNP", "LUMBAR", "FIJACION", "LAMINECTOMIA", "ARTROLISIS"]):
        return "Neurocirugía / Columna"
    if any(x in i for x in ["TIROIDECTOMIA", "PARATIROIDES"]):
        return "Cirugía Endocrina"
    if any(x in i for x in ["PROSTATECTOMIA", "ADENOMECTOMIA", "RTU", "VASECTOMIA", "CIRCUNCISION", "CPRE", "CISTOSCOPIA"]):
        return "Urología / Endoscopía"
    if any(x in i for x in ["LIO", "CATARATA", "PTERIGION", "VITREO"]):
        return "Oftalmología"
    
    return "Otras"



# 3. INGESTA MULTI-MENSUAL

def leer_mes(path: Path) -> pd.DataFrame:
    """Lee un archivo de operaciones de pabellón y extrae metadata de la ruta."""
    df = pd.read_excel(path, engine="openpyxl", sheet_name="Procesado")
    df = df.dropna(subset=["Fecha", "Cirujano"])      # Limpiar filas vacías fantasma
    df.columns = df.columns.str.strip()               # Limpiar headers
    
    df["archivo_origen"] = path.stem
    
    # Extracción del mes desde el nombre del archivo (Ej: 2025_11)
    match = re.search(r"(\d{4}_\d{2})", path.stem)
    if match:
        df["Mes_Origen"] = match.group(1).replace("_", "-")
    else:
        df["Mes_Origen"] = "DESCONOCIDO"
        
    return df


def cargar_todos_los_meses(input_dir: Path) -> pd.DataFrame:
    """Busca y concatena todos los archivos mensuales de pabellón en un solo DataFrame."""
    archivos = sorted(input_dir.glob("*.xlsm")) + sorted(input_dir.glob("*.xlsx"))
    
    if not archivos:
        print(f"[WARN] No se encontraron archivos en {input_dir}. Usando archivo único de ejemplo.")
        return None
        
    dfs = [leer_mes(p) for p in archivos]
    df = pd.concat(dfs, ignore_index=True)
    print(f"[INFO] Cargados {len(archivos)} archivos → {len(df)} filas totales.")
    return df



# 4. LIMPIEZA Y ENRIQUECIMIENTO (ETL Core)

def limpiar(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica transformaciones de tiempo, reglas de negocio y banderas lógicas."""
    
    # 4.1 Tratamiento de Fechas
    df["Fecha"] = pd.to_datetime(df["Fecha"], errors="coerce")
    df["Mes"]   = df["Fecha"].dt.to_period("M").astype(str)
    df["DiaSemana"] = df["Fecha"].dt.day_name()
    df["Semana"] = df["Fecha"].dt.isocalendar().week.astype(int)

    # 4.2 Normalización de Entidades
    df["Cirujano"] = df["Cirujano"].str.strip().str.upper().str.replace(r'\s+', ' ', regex=True)
    df["Pabellon"] = pd.to_numeric(df["N° Pabellon2"], errors="coerce").fillna(
        pd.to_numeric(df["N° Pabellon"], errors="coerce")
    )

    # 4.3 Banderas lógicas (Flags)
    df["es_bariatrica"] = df["bar"].str.strip().str.upper().eq("SI")
    df["es_primera"]    = df["PRIMERA?"].str.strip().str.upper().isin(["SI", "SÍ"])

    # 4.4 Intervención normalizada y especialidad
    df["Intervencion_Clean"] = df["Intervencion corta"].map(normalizar_intervencion)
    df["Especialidad"]       = df["Intervencion_Clean"].map(clasificar_especialidad)
    df["Intervencion_Raw"]   = df["Intervencion corta"]

    # 4.5 Métricas temporales derivadas (minutos)
    df["T_quirurgico"]      = duration(df, "ini cx",    "fin cx")    # Bisturí puro
    df["T_montaje"]         = duration(df, "montaje",   "fin mont")  # Preparación OR
    df["T_prequirurgico"]   = duration(df, "montaje",   "ini cx")    # Montaje → Bisturí
    df["T_postquirurgico"]  = duration(df, "fin cx",    "fin aseo")  # Bisturí → OR libre
    df["T_anestesia"]       = duration(df, "ini anes",  "Fin anes")  # Duración anestesia
    df["T_ingreso_pac"]     = duration(df, "ing pac",   "ini cx")    # Espera paciente
    df["T_total_real"]      = duration(df, "montaje",   "fin aseo")  # Ocupación real OR
    df["T_no_quirurgico"]   = df["T_total_real"] - df["T_quirurgico"]

    # 4.6 Delta Programado vs Real
    df["T_programado_min"] = df["Programado"].map(to_minutes)
    df["Delta_min"] = df["T_total_real"] - df["T_programado_min"]

    # 4.7 Clasificación SLA de duración (± holgura)
    df["Estado_SLA"] = pd.cut(
        df["Delta_min"],
        bins=[-np.inf, -HOLGURA_MIN - 1, HOLGURA_MIN, np.inf],
        labels=["Bajo Hora (Muy Rápida)", "En Hora", "Atraso"]
    )

    # 4.8 Densidad operatoria (% de tiempo que es realmente bisturí)
    df["Densidad_operatoria"] = (df["T_quirurgico"] / df["T_total_real"]).clip(0, 1)

    # 4.9 Módulo de Puntualidad de Inicio (Anclado a Bisturí Virgen)
    df['H_prog_min']       = df['Hora inicio programada'].map(to_minutes)
    df['H_real_min']       = df['ini cx'].map(to_minutes) 
    df['Delta_Inicio_min'] = df['H_real_min'] - df['H_prog_min']
    
    # Corrección cruce medianoche
    df['Delta_Inicio_min'] = df['Delta_Inicio_min'].where(
        df['Delta_Inicio_min'] > -120, 
        df['Delta_Inicio_min'] + 1440
    )

    # Clasificación de Puntualidad (Incluye tiempo de preparación)
    df['Puntualidad_Inicio'] = pd.cut(
        df['Delta_Inicio_min'],
        bins=[-np.inf, 0, 45, 75, 120, np.inf],
        labels=['Adelantado', 'A tiempo (Prep normal)', 'Atraso Leve', 'Atraso Moderado', 'Crítico (>2 horas)']
    )
    
    # Atribución de la causa del retraso (Cascada vs Clínica)
    df['Causa_Retraso'] = np.where(
        df['es_primera'], 'Clínica (Primera cx del día)',
        np.where(df['Delta_Inicio_min'] > 45, 'Cascada (Hereda de anterior)', 'Sin retraso')
    )

    # 4.10 Coeficiente de Variación (CV) Global por cirujano
    cv_cir = (
        df.groupby("Cirujano")["T_total_real"]
        .agg(lambda x: x.std() / x.mean() if x.mean() > 0 else np.nan)
        .rename("CV_cirujano")
    )
    df = df.merge(cv_cir, on="Cirujano", how="left")

    # 4.11 Detección Turno Hábil vs Inhábil (Urgencias)
    df['Fuera_Horario_Habil'] = np.where(
        (df['H_real_min'] < 420) | (df['H_real_min'] >= 1380),
        "Turno Noche / Urgencia",
        "Horario Hábil (07:00-23:00)"
    )

    return df



# 5. MODELO NEWSVENDOR (Asignación de Capacidad bajo Incertidumbre)

def calcular_newsvendor(df: pd.DataFrame, CR_TARGET: float) -> pd.DataFrame:
    """
    Evalúa el tiempo óptimo a programar balanceando el costo de ocio vs atraso (Critical Ratio).
    Exige N>=5 para validez estadística y limpia outliers (IQR).
    """
    # 1. Aislamiento de Outliers (IQR) local para el modelo
    grp = df.groupby(["Cirujano", "Intervencion_Clean"])["T_total_real"]
    n_casos = grp.transform("count")
    q1 = grp.transform(lambda x: x.quantile(0.25))
    q3 = grp.transform(lambda x: x.quantile(0.75))
    
    limite = np.minimum(q3 + 3 * (q3 - q1), 960)
    mask = (n_casos < 3) | (df["T_total_real"] <= limite)
    df_model = df[mask].copy()

    # 2. Cálculo Estadístico
    def stats(x):
        return pd.Series({
            "N":          x.count(),
            "Media_real": x.mean(),
            "Std_real":   x.std(),
            "CV":         x.std() / x.mean() if x.mean() > 0 else np.nan,
            "P25":        x.quantile(0.25),
            "P50":        x.quantile(0.50),
            "P_OPTIMO":   x.quantile(CR_TARGET),
            "P85":        x.quantile(0.85),
            "P90":        x.quantile(0.90),
        })

    nv = (
        df_model.dropna(subset=["T_total_real", "T_programado_min"])
        .groupby(["Intervencion_Clean", "Cirujano"])
        ["T_total_real"]
        .apply(stats)
        .reset_index()
        .rename(columns={"level_2": "metric"})
    )
    
    if "metric" in nv.columns:
        nv = nv.pivot_table(
            index=["Intervencion_Clean", "Cirujano"],
            columns="metric", values="T_total_real", aggfunc="first"
        ).reset_index()
        nv.columns.name = None

    prog = (
        df_model.groupby(["Intervencion_Clean", "Cirujano"])["T_programado_min"]
        .median()
        .rename("Prog_actual_mediana")
        .reset_index()
    )
    nv = nv.merge(prog, on=["Intervencion_Clean", "Cirujano"])
    
    # Rigor estadístico: N>=5
    nv = nv[nv["N"] >= 5] 

    # Diagnóstico Final
    nv["Tiempo_optimo_Newsvendor"] = nv["P_OPTIMO"]
    nv["Brecha_min"]               = nv["P_OPTIMO"] - nv["Prog_actual_mediana"]
    nv["Subprogramada"]            = nv["Brecha_min"] > HOLGURA_MIN
    nv["Sobreprogramada"]          = nv["Brecha_min"] < -HOLGURA_MIN
    nv["Efectividad_Teorica"]      = CR_TARGET

    return nv.sort_values("N", ascending=False)



# 6. MODELO OEE (OVERALL EQUIPMENT EFFECTIVENESS)

def calcular_oee(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula el OEE Quirúrgico dinámico agrupado por Pabellón y Fecha.
    Disponibilidad: Uso real / Capacidad bloque programado
    Rendimiento: Bisturí puro / Tiempo total ocupado
    Calidad: Cumplimiento SLA (En hora)
    """
    df_temp = df.copy()
    df_temp['Fin_Prog_min'] = df_temp['H_prog_min'] + df_temp['T_programado_min']

    g = df_temp.groupby(["Fecha", "Pabellon"])

    oee = pd.DataFrame({
        "T_total_real_sum":     g["T_total_real"].sum(),
        "T_quirurgico_sum":     g["T_quirurgico"].sum(),
        "N_cirugias":           g["T_total_real"].count(),
        "N_en_hora":            g["Estado_SLA"].apply(lambda x: (x == "En Hora").sum()),
        "N_sla_clinico":        g["Estado_SLA"].apply(lambda x: x.isin(["En Hora", "Bajo Hora (Muy Rápida)"]).sum()),
        "Apertura_Teorica_min": g["H_prog_min"].min(),
        "Cierre_Teorico_min":   g["Fin_Prog_min"].max()
    }).reset_index()

    # Capacidad teórica del bloque
    oee["Capacidad_Min_Dia"] = oee["Cierre_Teorico_min"] - oee["Apertura_Teorica_min"]
    oee["Capacidad_Min_Dia"] = np.where(oee["Capacidad_Min_Dia"] <= 0, 1, oee["Capacidad_Min_Dia"])

    # Componentes OEE
    oee["Disponibilidad"]  = oee["T_total_real_sum"] / oee["Capacidad_Min_Dia"] 
    oee["Rendimiento"]     = (oee["T_quirurgico_sum"] / oee["T_total_real_sum"]).clip(0, 1)
    oee["Calidad"]         = (oee["N_en_hora"] / oee["N_cirugias"]).clip(0, 1)
    
    oee["SLA_Clinico_Pct"] = (oee["N_sla_clinico"] / oee["N_cirugias"]).clip(0, 1)
    oee["OEE"]             = oee["Disponibilidad"] * oee["Rendimiento"] * oee["Calidad"]

    oee["Clasificacion_OEE"] = pd.cut(
        oee["OEE"],
        bins=[-np.inf, 0.50, 0.65, 0.80, np.inf],
        labels=["Crítico (<50%)", "Bajo (50–65%)", "Aceptable (65–80%)", "World Class (>80%)"]
    )
    
    return oee



# 7. MODELO SPC (STATISTICAL PROCESS CONTROL)

def calcular_spc(df: pd.DataFrame) -> pd.DataFrame:
    """
    Control Estadístico del proceso quirúrgico basado en distribución Log-Normal.
    Evalúa alertas por Límite de 3 Sigma y la Regla 2 de Nelson.
    """
    # 1. Filtros y Transformación Log-Normal
    grp = df.groupby(["Cirujano", "Intervencion_Clean"])["T_total_real"]
    q1 = grp.transform(lambda x: x.quantile(0.25))
    q3 = grp.transform(lambda x: x.quantile(0.75))
    limite = np.minimum(q3 + 3 * (q3 - q1), 720)
    mask = (grp.transform("count") < 3) | (df["T_total_real"] <= limite)
    
    df_spc_input = df[mask & (df["T_total_real"] > 0)].copy()
    df_spc_input['log_T'] = np.log(df_spc_input['T_total_real'])

    # 2. Límites Matemáticos
    stats_log = (
        df_spc_input.groupby("Cirujano")["log_T"]
        .agg(N="count", Media_log="mean", Std_log="std")
        .reset_index()
    )

    stats_log["UCL_log"] = stats_log["Media_log"] + 3 * stats_log["Std_log"]
    stats_log["LCL_log"] = stats_log["Media_log"] - 3 * stats_log["Std_log"]

    stats_log["CL"]  = np.exp(stats_log["Media_log"])
    stats_log["UCL"] = np.exp(stats_log["UCL_log"])
    stats_log["LCL"] = np.exp(stats_log["LCL_log"])

    # 3. Evaluación Reglas de Nelson (Ventana 8 casos)
    df_spc_input = df_spc_input.sort_values(["Cirujano", "Fecha"])
    
    def check_nelson_rule_2(group):
        if len(group) < 8:
            return False
        last_8 = group['T_total_real'].tail(8)
        cl = group['T_total_real'].median() 
        all_above = all(x > cl for x in last_8)
        all_below = all(x < cl for x in last_8)
        return all_above or all_below

    nelson_flags = (
        df_spc_input.groupby("Cirujano")
        .apply(check_nelson_rule_2, include_groups=False)
        .rename("Alerta_Nelson_R2")
        .reset_index()
    )

    stats_log = stats_log.merge(nelson_flags, on="Cirujano", how="left")
    media_real = df_spc_input.groupby("Cirujano")["T_total_real"].mean().rename("Media_Aritmetica")
    stats_log = stats_log.merge(media_real, on="Cirujano", how="left")

    return stats_log[stats_log["N"] >= 15].sort_values("N", ascending=False)



# 8. TABLA SIMULADOR POWER BI (What-If Parameters)

def calcular_tabla_simulador(df: pd.DataFrame) -> pd.DataFrame:
    """
    Genera una tabla desnormalizada (larga) de percentiles para alimentar
    un Slicer interactivo de niveles de servicio (50% al 95%) en Power BI.
    """
    rows = []
    percentiles = [50, 55, 60, 65, 70, 75, 80, 85, 90, 95]
    for (interv, cir), grp in df.dropna(subset=["T_total_real"]).groupby(["Intervencion_Clean", "Cirujano"]):
        if len(grp) < 3:
            continue
        for p in percentiles:
            t = grp["T_total_real"].quantile(p / 100)
            rows.append({
                "Intervencion_Clean":   interv,
                "Cirujano":             cir,
                "Nivel_servicio_pct":   p,
                "Tiempo_programar_min": round(t, 0),
                "Tiempo_programar_hhmm": f"{int(t)//60}:{int(t)%60:02d}",
                "N_casos":              len(grp),
            })
    return pd.DataFrame(rows)



# 9. CONTROLADOR PRINCIPAL (MAIN)

def main():
    archivos_presentes = list(INPUT_DIR.glob("*.xls*"))

    if INPUT_DIR.exists() and len(archivos_presentes) > 0:
        print(f"[INFO] Cargando {len(archivos_presentes)} archivos desde {INPUT_DIR}...")
        df_raw = cargar_todos_los_meses(INPUT_DIR)
    else:
        print(f"[ERROR] No se encontraron archivos en {INPUT_DIR}")
        return

    # Limpieza base
    df_raw = df_raw.dropna(subset=["Fecha", "Cirujano"])
    df = limpiar(df_raw)
    
    print(f"[INFO] Filas limpias: {len(df)} | Cirujanos: {df['Cirujano'].nunique()} "
          f"| Intervenciones canónicas: {df['Intervencion_Clean'].nunique()}")

    # Carga de Costos para Critical Ratio (Newsvendor)
    path_costos = Path(__file__).resolve().parent / "Parametros_Costos.xlsx"
    try:
        df_costos = pd.read_excel(path_costos)
        c_o = df_costos.loc[df_costos['ID_Parametro'].str.strip() == 'COSTO_OCIO', 'Valor'].values[0]
        c_u = df_costos.loc[df_costos['ID_Parametro'].str.strip() == 'COSTO_ATRASO', 'Valor'].values[0]
        CR_TARGET = c_u / (c_u + c_o)
        print(f"Configuración cargada: CR = {CR_TARGET:.2f} (Fuente: {path_costos.name})")

    except Exception as e:
        CR_TARGET = 0.70
        print(f"No se encontró {path_costos.name}. Usando CR por defecto 0.70. Error: {e}")

    # Ejecución de Modelos
    df_nv  = calcular_newsvendor(df, CR_TARGET)
    df_oee = calcular_oee(df)
    df_spc = calcular_spc(df)
    df_sim = calcular_tabla_simulador(df)

    # Diagnóstico Consola
    print("\n[DIAGNÓSTICO] Top 10 intervenciones más SUBPROGRAMADAS (Optimización Newsvendor):")
    sub = df_nv[df_nv["Subprogramada"]].sort_values("Brecha_min", ascending=False)
    print(sub[[
        "Intervencion_Clean", 
        "Cirujano", 
        "N", 
        "Prog_actual_mediana",
        "Tiempo_optimo_Newsvendor",
        "Brecha_min"
    ]].head(10).to_string(index=False))

    print("\n[DIAGNÓSTICO] OEE promedio por pabellón:")
    print(df_oee.groupby("Pabellon")["OEE"].mean().round(3).to_string())
    
    # ── Exportación y Cierre ─────────────────────────────────────────────
    
    cols_hechos = [
        "Fecha", "Mes", "DiaSemana", "Semana", "Pabellon", "archivo_origen",
        "Cirujano", "Intervencion_Clean", "Intervencion_Raw", "Especialidad",
        "es_bariatrica", "es_primera",
        "T_total_real", "T_quirurgico", "T_montaje",
        "T_prequirurgico", "T_postquirurgico", "T_anestesia",
        "T_no_quirurgico", "T_ingreso_pac",
        "Densidad_operatoria", "CV_cirujano",
        "Delta_min", "Estado_SLA",
        "H_prog_min", "H_real_min",
        "Delta_Inicio_min", "Puntualidad_Inicio", "Causa_Retraso",
        "Fuera_Horario_Habil", "Clave_Unica"
    ]
    
    df['Clave_Unica'] = df['Cirujano'].astype(str) + "|" + df['Intervencion_Clean'].astype(str)
    cols_hechos = [c for c in cols_hechos if c in df.columns]

    # Protocolo Anti-Decimales para Power BI (Limpieza de Formatos)
    cols_tiempo_hechos = ['T_total_real', 'T_quirurgico', 'T_montaje', 'T_prequirurgico', 
                          'T_postquirurgico', 'T_anestesia', 'T_no_quirurgico', 'T_ingreso_pac',
                          'H_prog_min', 'H_real_min', 'Delta_Inicio_min']
    for col in cols_tiempo_hechos:
        if col in df.columns:
            df[col] = df[col].fillna(0).round(0).astype(int)

    cols_tiempo_nv = ['Media_real', 'P25', 'P50', 'P_OPTIMO', 'P85', 'P90', 
                      'Prog_actual_mediana', 'Tiempo_optimo_Newsvendor', 'Brecha_min']
    for col in cols_tiempo_nv:
        if col in df_nv.columns:
            df_nv[col] = df_nv[col].fillna(0).round(0).astype(int)

    if 'Tiempo_programar_min' in df_sim.columns:
        df_sim['Tiempo_programar_min'] = df_sim['Tiempo_programar_min'].fillna(0).round(0).astype(int)

    print(f"\n[INFO] Exportando archivos finales a: {OUTPUT_DIR.resolve()}")

    # Bandeja de Excepciones Analíticas
    mask_excepciones = df['Intervencion_Clean'].str.startswith("REQUIERE_REVISION", na=False)
    df_excepciones = df[mask_excepciones]
    
    if not df_excepciones.empty:
        print(f"\nALERTA OPERATIVA Se detectaron {len(df_excepciones)} registros con intervenciones no reconocidas.")
        resumen = df_excepciones.groupby(['Intervencion_Raw', 'Cirujano']).size().reset_index(name='Frecuencia')
        resumen = resumen.sort_values('Frecuencia', ascending=False)
        resumen.to_csv(OUTPUT_DIR / "ALERTA_NUEVAS_INTERVENCIONES.csv", index=False, encoding="utf-8-sig")
        print(f"Archivo generado: {OUTPUT_DIR.name}/ALERTA_NUEVAS_INTERVENCIONES.csv")
    else:
        print("\nMATCH PERFECTO Todas las cirugías fueron reconocidas por el algoritmo Fuzzy.")
    
    df[cols_hechos].to_csv(OUTPUT_DIR / "hechos_pabellon.csv", index=False)
    df_nv.to_csv(OUTPUT_DIR / "newsvendor_optimo.csv", index=False)
    df_oee.to_csv(OUTPUT_DIR / "oee_pabellon.csv", index=False)
    df_spc.to_csv(OUTPUT_DIR / "spc_cirujano.csv", index=False)
    df_sim.to_csv(OUTPUT_DIR / "simulador_cr.csv", index=False)

    

if __name__ == "__main__":
    main()