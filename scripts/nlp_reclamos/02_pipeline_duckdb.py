import logging
import time
import pandas as pd
import duckdb
from pathlib import Path
from typing import Optional


# CONFIGURACIÓN Y LOGGING

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# FUNCIONES DE LIMPIEZA Y TRANSFORMACIÓN

def limpiar_rut_universal(rut: any) -> Optional[str]:
    """Limpia y estandariza RUT removiendo ceros a la izquierda y caracteres especiales"""
    if pd.isna(rut): 
        return None
    rut_str = str(rut).upper().replace('.0', '').replace('.', '').replace('-', '').replace(' ', '')
    rut_str = rut_str.lstrip('0')
    return rut_str if rut_str else None

def estandarizar_fechas(serie: pd.Series) -> pd.Series:
    """Normaliza múltiples formatos de fecha """
    s = serie.astype(str).str.replace(r' \(Coordinated Universal Time\)', '', regex=True)
    s = s.str.replace(r'GMT\+0000', '', regex=True).str.strip()
    
    f1 = pd.to_datetime(s, format='%d-%m-%Y', errors='coerce') 
    f2 = pd.to_datetime(s, format='%d/%m/%Y', errors='coerce') 
    f3 = pd.to_datetime(s, format='%Y-%m-%d %H:%M:%S', errors='coerce') 
    f4 = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce') 
    f_auto = pd.to_datetime(s, dayfirst=True, errors='coerce') 
    
    return f1.fillna(f2).fillna(f3).fillna(f4).fillna(f_auto)



# FUNCIONES DE EXTRACCIÓN

def cargar_archivos_directorio(patron_busqueda: str) -> pd.DataFrame:
    """Busca y concatena archivos Excel/CSV según un patrón"""
    archivos = list(Path('.').glob(patron_busqueda))
    lista_df = []
    
    for f in archivos:
        if f.name.startswith('~$'): 
            continue
        try:
            engine = 'xlrd' if f.suffix == '.xls' else 'openpyxl'
            df = pd.read_excel(f, engine=engine)
        except Exception:
            # Fallback para archivos tabulares mal formados
            df = pd.read_csv(f, sep='\t', encoding='latin-1', on_bad_lines='skip', low_memory=False)
        lista_df.append(df)
    
    if not lista_df:
        return pd.DataFrame()
        
    return pd.concat(lista_df, ignore_index=True)



# PIPELINE PRINCIPAL

def ejecutar_pipeline():
    start_time = time.time()
    logging.info("Iniciando DuckDB")
    
    # existencia de directorios
    Path('output').mkdir(exist_ok=True)
    
    con = duckdb.connect('modelo_reclamos.duckdb')
    
    
    # 1. INGESTA Y LIMPIEZA UNIVERSAL
    logging.info("1. Cargando y limpiando bases de datos")
    
    # A) Datos Operacionales (MasterKey)
    df_mk = cargar_archivos_directorio('data/MK_*.xls*')
    df_est = cargar_archivos_directorio('data/Estadistica_*.xls*')
    df_mk = pd.concat([df_mk, df_est], ignore_index=True) if not df_est.empty else df_mk
    
    if not df_mk.empty:
        df_mk['FECHADMISION'] = pd.to_datetime(df_mk['FECHADMISION'], dayfirst=True, errors='coerce')
        df_mk['FECHA'] = pd.to_datetime(df_mk['FECHA'], dayfirst=True, errors='coerce')
        df_mk['CODADMISION'] = df_mk['CODADMISION'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
        df_mk['RUT_CLEAN'] = df_mk['RUTPACIENTE'].apply(limpiar_rut_universal)
        con.register('mk_raw', df_mk)

    # B) Datos de Satisfacción (CASS)
    df_cass = cargar_archivos_directorio('data/Descarga_BD_*.xls*')
    if not df_cass.empty:
        for col in df_cass.select_dtypes(include=['object', 'datetime64[ns]']).columns:
            df_cass[col] = df_cass[col].astype(str)
        con.register('cass_raw', df_cass)

    # C) Datos de Reclamos (NLP Output)
    path_expedientes = Path('staging/expedientes_enriquecidos.xlsx')
    if path_expedientes.exists():
        df_exp = pd.read_excel(path_expedientes)
        df_exp['fechaHechos'] = estandarizar_fechas(df_exp['fechaHechos'])
        df_exp['fechaRecepcionReclamo'] = estandarizar_fechas(df_exp['fechaRecepcionReclamo'])
        df_exp['fechaIngreso'] = estandarizar_fechas(df_exp['fechaIngreso'])
        
        df_exp['fecha_cruce_reclamo'] = df_exp['fechaHechos'].fillna(df_exp['fechaRecepcionReclamo']).fillna(df_exp['fechaIngreso'])
        df_exp['rutPaciente'] = df_exp['rutPaciente'].fillna(df_exp.get('rutReclamante'))
        df_exp['RUT_CLEAN'] = df_exp['rutPaciente'].apply(limpiar_rut_universal)
        con.register('expedientes_raw', df_exp)

    
    # 2. MOTOR SQL
    logging.info("2. Ejecutando agrupaciones clínicas y jerarquía (Episodios)")
    
    con.execute("""
        CREATE OR REPLACE TABLE episodios AS
        WITH mk_limpia AS (
            SELECT 
                *,
                COALESCE(CAST(FECHA AS DATE), CAST(FECHADMISION AS DATE)) as fecha_actividad
            FROM mk_raw
            WHERE RUT_CLEAN IS NOT NULL AND RUT_CLEAN != 'NAN' AND RUT_CLEAN != ''
        ),
        episodios_agrupados AS (
            SELECT 
                CASE 
                    WHEN TRY_CAST(CODADMISION AS INTEGER) = 0 THEN '0-' || RUT_CLEAN || '-' || CAST(fecha_actividad AS VARCHAR)
                    ELSE CAST(CODADMISION AS VARCHAR) 
                END as episodio_id,
                RUT_CLEAN as rut,
                MAX(NOMPACIENTE) as nombre_paciente,
                MIN(CAST(FECHADMISION AS DATE)) as fecha_admision_historica,
                MAX(fecha_actividad) as fecha_servicio_real,
                STRING_AGG(UPPER(CAST(NOMCCOSTO AS VARCHAR)), ' | ') as lista_costos,
                STRING_AGG(UPPER(CAST(NOMAGRUP AS VARCHAR)), ' | ') as lista_agrup,
                STRING_AGG(UPPER(CAST(NOMESPECIALIDAD AS VARCHAR)), ' | ') as lista_espe,
                STRING_AGG(UPPER(CAST(ORIGEN AS VARCHAR)), ' | ') as lista_origenes,
                STRING_AGG(UPPER(CAST(NOMDESTINO AS VARCHAR)), ' | ') as lista_destinos,
                COUNT(*) as num_actividades
            FROM mk_limpia
            WHERE 
              (
                TRY_CAST(CODADMISION AS INTEGER) >= 10000 
                OR 
                (
                    TRY_CAST(CODADMISION AS INTEGER) = 0 
                    AND (
                        UPPER(CAST(ORIGEN AS VARCHAR)) IN ('CENTRO MEDICO', 'URGENCIA', 'AMBULATORIO')
                        OR (CAST(CAJA AS VARCHAR) = '61' AND UPPER(CAST(NOMPACIENTE AS VARCHAR)) LIKE '%VIRTUAL%')
                    )
                )
              )
            GROUP BY 
                CASE 
                    WHEN TRY_CAST(CODADMISION AS INTEGER) = 0 THEN '0-' || RUT_CLEAN || '-' || CAST(fecha_actividad AS VARCHAR)
                    ELSE CAST(CODADMISION AS VARCHAR) 
                END,
                RUT_CLEAN
        )
        SELECT 
            episodio_id, rut, nombre_paciente, fecha_admision_historica, fecha_servicio_real,
            CASE 
                WHEN lista_agrup LIKE '%HOSPITALIZACION COMPLEJA%' 
                  OR lista_costos LIKE '%UPC%' OR lista_costos LIKE '%UTI%' OR lista_costos LIKE '%UCI%' OR lista_costos LIKE '%INTENSIV%' 
                  THEN 'UPC (UTI, UCI)'
                WHEN lista_agrup LIKE '%HOSPITALIZACION%' OR lista_agrup LIKE '%CUIDADORES HOSPITALIZADO%'
                  OR lista_origenes LIKE '%HOSPITALIZACION%' OR lista_destinos LIKE '%CIRUGIA%' OR lista_destinos LIKE '%MEDICINA%' OR lista_costos LIKE '%HOSPITALIZADO%' 
                  THEN 'Hospitalizados - Médico Quirúrgico'
                WHEN lista_agrup LIKE '%URGENCIA%' OR lista_origenes LIKE '%URGENCIA%' THEN 'Urgencias'
                WHEN lista_agrup LIKE '%ARSENALERA%' OR lista_agrup LIKE '%DERECHO PABELLON%' OR lista_agrup LIKE '%IMPLANTES / TORNILLOS%'
                  OR lista_costos LIKE '%PABELLON%' OR lista_costos LIKE '%PABELLÓN%' THEN 'Pabellón'
                WHEN lista_agrup LIKE '%ENDOSCOPIA%' OR lista_agrup LIKE '%COLONOSCOPIA%' OR lista_costos LIKE '%GASTRO%' THEN 'Colonoscopía / Endoscopía'
                WHEN lista_agrup LIKE '%SCANNER%' OR lista_agrup LIKE '%ECOTOMOGRAFIA%' OR lista_agrup LIKE '%RAYOS%' OR lista_agrup LIKE '%RESONANCIA%' OR lista_agrup LIKE '%MAMOGRAFIA%' OR lista_agrup LIKE '%MEDICINA NUCLEAR%'
                  OR lista_espe LIKE '%IMAGENOLOGIA%' OR lista_espe LIKE '%RADIOLOGO%' OR lista_costos LIKE '%IMAGENOLOGIA%' THEN 'Imagenología'
                WHEN lista_agrup LIKE '%LABORATORIO%' OR lista_agrup LIKE '%ANATOMIA PATOLOGICA%' OR lista_agrup LIKE '%BANCO SANGRE%'
                  OR lista_espe LIKE '%TECNOLOGO MEDICO%' OR lista_costos LIKE '%LABORATORIO%' THEN 'Laboratorio / Toma de muestras'
                WHEN lista_agrup LIKE '%KINESIOLOGIA%' OR lista_agrup LIKE '%TERAPIA OCUPACIONAL%' OR lista_espe LIKE '%KINESIOLOGIA%' THEN 'Kinesiología'
                WHEN lista_agrup LIKE '%CONSULTA%' OR lista_agrup LIKE '%HON. MEDICOS%' OR lista_agrup LIKE '%PROCEDIMIENTO%' OR lista_agrup LIKE '%ELECTRO%' OR lista_agrup LIKE '%VACUNATORIO%' OR lista_agrup LIKE '%CARDIOLOGIC%'
                  OR lista_origenes LIKE '%CENTRO MEDICO%' OR lista_origenes LIKE '%AMBULATORIO%' OR lista_costos LIKE '%CENTRO MEDICO%' OR lista_costos LIKE '%CONSULTA MED%' THEN 'Consulta médica'
                WHEN lista_agrup LIKE '%MEDICAMENTO%' OR lista_agrup LIKE '%INSUMO%' OR lista_costos LIKE '%FARMACIA%' THEN 'Farmacia Venta Público'
                WHEN lista_agrup LIKE '%ESTERILIZACION%' OR lista_costos LIKE '%ESTERILIZACION%' THEN 'Esterilización'
                WHEN lista_costos LIKE '%SERVICIOS GENERALES%' OR lista_costos LIKE '%ROPERIA%' THEN 'Servicios generales - Infraestructura'
                ELSE 'Otra'
            END as unidad,
            num_actividades
        FROM episodios_agrupados
    """)

    logging.info("3. Calculando métricas de volumen")
    con.execute("""
        CREATE OR REPLACE TABLE tru_por_unidad AS
        SELECT 
            unidad,
            COUNT(DISTINCT episodio_id) as total_atenciones,
            SUM(num_actividades) as volumen_actividades,
            COUNT(DISTINCT rut) as pacientes_unicos
        FROM episodios
        GROUP BY unidad
    """)

    logging.info("4. Vinculando Reclamos operacionales (-60 a +30 días)...")
    con.execute("""
        CREATE OR REPLACE TABLE expedientes_vinculados AS
        SELECT 
            ROW_NUMBER() OVER() as id_reclamo_unico,
            e.*,
            (
                SELECT mk.episodio_id
                FROM episodios mk
                WHERE mk.rut = e.RUT_CLEAN
                  AND mk.fecha_servicio_real >= CAST(e.fecha_cruce_reclamo AS DATE) - INTERVAL 60 DAY
                  AND mk.fecha_servicio_real <= CAST(e.fecha_cruce_reclamo AS DATE) + INTERVAL 30 DAY
                ORDER BY ABS(CAST(mk.fecha_servicio_real AS DATE) - CAST(e.fecha_cruce_reclamo AS DATE))
                LIMIT 1
            ) as episodio_vinculado
        FROM expedientes_raw e
    """)

    if not df_cass.empty:
        logging.info("5. Procesando encuestas de calidad (CASS) y transformando dimensiones...")
        con.execute("""
            CREATE OR REPLACE TABLE cass_vinculado AS
            SELECT 
                c.*,
                LTRIM(UPPER(REPLACE(REPLACE(REPLACE(REPLACE(CAST(c.rut_paciente AS VARCHAR), '.0', ''), '.', ''), '-', ''), ' ', '')), '0') as rut_clean,
                TRY_CAST(REPLACE(CAST(c.NPS AS VARCHAR), ',', '.') AS DOUBLE) as nps_num,
                TRY_CAST(REPLACE(CAST(c.ponderador_mensual AS VARCHAR), ',', '.') AS DOUBLE) as peso_voto,
                CASE 
                    WHEN UPPER(c.segmento) = 'URGENCIA' THEN 'Urgencias'
                    WHEN UPPER(c.segmento) = 'HOSPITALIZADO' THEN 'Hospitalizados - Médico Quirúrgico'
                    WHEN UPPER(c.segmento) = 'LABORATORIO' THEN 'Laboratorio / Toma de muestras'
                    WHEN UPPER(c.segmento) = 'KINESIOLOGIA' THEN 'Kinesiología'
                    WHEN UPPER(c.segmento) = 'IMAGENOLOGIA' THEN 'Imagenología'
                    WHEN UPPER(c.segmento) = 'CENTRO MEDICO' THEN 'Consulta médica'
                    WHEN UPPER(c.segmento) = 'PROCEDIMIENTOS' THEN 
                        CASE 
                            WHEN UPPER(c.prestacion) IN ('GASTROENTEROLOGIA', 'COLOPROCTOLOGIA', 'CIRUGIA DIGESTIVA Y BARIÁTRICA') THEN 'Colonoscopía / Endoscopía'
                            ELSE 'Consulta médica' 
                        END
                    ELSE 'Otra'
                END as unidad_cass,
                (
                    SELECT mk.episodio_id FROM episodios mk
                    WHERE mk.rut = LTRIM(UPPER(REPLACE(REPLACE(REPLACE(REPLACE(CAST(c.rut_paciente AS VARCHAR), '.0', ''), '.', ''), '-', ''), ' ', '')), '0')
                      AND mk.fecha_servicio_real >= TRY_CAST(c.fecha_prestacion AS DATE) - INTERVAL 15 DAY
                      AND mk.fecha_servicio_real <= TRY_CAST(c.fecha_prestacion AS DATE) + INTERVAL 5 DAY
                    ORDER BY ABS(CAST(mk.fecha_servicio_real AS DATE) - CAST(c.fecha_prestacion AS DATE))
                    LIMIT 1
                ) as episodio_vinculado
            FROM cass_raw c
            WHERE c.rut_paciente IS NOT NULL
        """)

        con.execute("""
            CREATE OR REPLACE TABLE cass_preguntas AS
            WITH unpivoted AS (
                UNPIVOT cass_vinculado
                ON "Agendamiento Consulta", "Infraestuctura Sala de Espera", "Tiempo de espera", "Atención Recepcionista", "Hora agendada", "Diagnóstico y Tratamiento", "Trato Médico", "Servicio Brindado", "Infraestructura box", "Enfermeras y Paramédicos", "Proceso de toma de exámenes o procedimientos", "Facilidad en Proceso de admisión", "Atención Personal de Admisión", "Infraestructura de la habitación", "Servicio de Alimentación", "Proceso de alta médica", "Claridad del plan de tratamiento", "Agendamiento examen imagenologia", "Infraestuctura Sala de Examen", "Atención Personal Clínico", "Infraestuctura Centro Kinesiologia", "Equipamiento", "Trato Kinesiologo", "Tratamiento Kinesiologo", "Infraestuctura Toma Muestras", "Trato Personal Clinico", "Toma de examenes"
                INTO NAME pregunta VALUE nota_raw
            )
            SELECT 
                episodio_vinculado, 
                unidad_cass, 
                rut_clean, 
                peso_voto, 
                TRY_CAST(fecha_prestacion AS DATE) as fecha_prestacion,
                pregunta,
                TRY_CAST(nota_raw AS INTEGER) as nota,
                CASE 
                    WHEN TRY_CAST(nota_raw AS INTEGER) IN (6, 7) THEN '1. Positiva (6-7)'
                    WHEN TRY_CAST(nota_raw AS INTEGER) = 5 THEN '2. Neutra (5)'
                    WHEN TRY_CAST(nota_raw AS INTEGER) IN (1, 2, 3, 4) THEN '3. Negativa (1-4)'
                    ELSE 'Sin Clasificar'
                END as clasificacion_nota
            FROM unpivoted
            WHERE TRY_CAST(nota_raw AS INTEGER) IS NOT NULL
        """)


   
    # 6. EXPORTACIÓN
    logging.info("6. Exportando tablas de hechos a formato Parquet")
    con.execute("COPY episodios TO 'output/fact_episodios.parquet' (FORMAT PARQUET)")
    con.execute("COPY tru_por_unidad TO 'output/fact_tru_unidades.parquet' (FORMAT PARQUET)")
    con.execute("COPY expedientes_vinculados TO 'output/fact_expedientes.parquet' (FORMAT PARQUET)")
    
    if not df_cass.empty:
        con.execute("COPY cass_vinculado TO 'output/fact_cass.parquet' (FORMAT PARQUET)")
        con.execute("COPY cass_preguntas TO 'output/fact_cass_preguntas.parquet' (FORMAT PARQUET)")

    # Validación final
    stats = con.execute("""
        SELECT 
            COUNT(*) as total,
            COUNT(episodio_vinculado) as vinculados,
            ROUND(100.0 * COUNT(episodio_vinculado) / COUNT(*), 1) as porcentaje
        FROM expedientes_vinculados
    """).fetchone()
    
    con.close()
    
    logging.info(f"VINCULACIÓN FINAL: {stats[2]}% ({stats[1]} de {stats[0]} reclamos vinculados al core)")
    logging.info(f"PIPELINE COMPLETADO EN {time.time() - start_time:.2f} SEGUNDOS")

if __name__ == "__main__":
    ejecutar_pipeline()