
import pandas as pd
from pathlib import Path
import logging
from faker import Faker

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
fake = Faker('es_CL')

def anonimizar_datos_reclamos():
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    OUTPUT_DIR = BASE_DIR / "output" / "nlp_reclamos"
    
    print("="*60)
    print("INICIANDO PROTOCOLO DE ENMASCARAMIENTO (NLP / CASS)")
    print("="*60)

    archivos_parquet = [
        "fact_episodios.parquet",
        "fact_expedientes.parquet",
        "fact_cass.parquet",
        "fact_cass_preguntas.parquet"
    ]

    ruts_unicos = set()
    funcionarios_unicos = set()
    dataframes = {}

    # 1. Recopilar entidades a enmascarar
    for archivo in archivos_parquet:
        ruta = OUTPUT_DIR / archivo
        if ruta.exists():
            df = pd.read_parquet(ruta)
            dataframes[archivo] = df
            
            # Recolectar RUTs (dependiendo del nombre de la columna)
            col_rut = 'rut' if 'rut' in df.columns else ('RUT_CLEAN' if 'RUT_CLEAN' in df.columns else ('rut_clean' if 'rut_clean' in df.columns else None))
            if col_rut:
                ruts_unicos.update(df[col_rut].dropna().unique())
            
            # Recolectar Nombres de Funcionarios detectados por la IA
            if 'Funcionario_Detectado' in df.columns:
                # Los nombres vienen separados por coma: "Dr Juan Perez, Enfermera Maria"
                nombres_raw = df['Funcionario_Detectado'].dropna().str.split(',')
                for lista in nombres_raw:
                    for nombre in lista:
                        if nombre.strip():
                            funcionarios_unicos.add(nombre.strip())

    if not ruts_unicos and not funcionarios_unicos:
        logging.warning("No se encontraron datos para anonimizar. Revisa los archivos de origen.")
        return

    # 2. Generar Diccionarios Criptográficos Estáticos
    mapa_ruts = {rut: fake.rut() for rut in ruts_unicos}
    
    mapa_funcionarios = {}
    for i, func in enumerate(sorted(funcionarios_unicos), start=1):
        if func.upper() == "HONORARIO / EXTERNO" or func == "":
            mapa_funcionarios[func] = func
        else:
            mapa_funcionarios[func] = f"Funcionario_{i:03d}"

    # 3. Aplicar Enmascaramiento y reconstruir Episodios
    for archivo, df in dataframes.items():
        cambios = 0
        
        # A. Enmascarar RUTs
        col_rut = 'rut' if 'rut' in df.columns else ('RUT_CLEAN' if 'RUT_CLEAN' in df.columns else ('rut_clean' if 'rut_clean' in df.columns else None))
        if col_rut:
            df[col_rut] = df[col_rut].map(mapa_ruts).fillna(df[col_rut])
            cambios += 1
            
        # B. Reconstruir episodio_id (ya que usa el RUT)
        # Formato: 0-RUT-FECHA o CODADMISION
        if 'episodio_id' in df.columns:
            def reconstruir_episodio(row):
                ep = str(row['episodio_id'])
                if ep.startswith('0-'):
                    partes = ep.split('-')
                    if len(partes) >= 3:
                        # Remplaza el rut viejo por el rut falso
                        rut_falso = row.get(col_rut, partes[1])
                        return f"0-{rut_falso}-{partes[2]}"
                return ep
            
            df['episodio_id'] = df.apply(reconstruir_episodio, axis=1)
            
        # C. Enmascarar episodio_vinculado (en expedientes y cass)
        if 'episodio_vinculado' in df.columns:
            def reconstruir_vinculado(row):
                ep = str(row['episodio_vinculado'])
                if ep.startswith('0-'):
                    partes = ep.split('-')
                    if len(partes) >= 3:
                        rut_falso = row.get(col_rut, partes[1])
                        return f"0-{rut_falso}-{partes[2]}"
                return ep
                
            df['episodio_vinculado'] = df.apply(reconstruir_vinculado, axis=1)

        # D. Enmascarar Funcionarios (Puede haber más de uno por fila, separados por coma)
        if 'Funcionario_Detectado' in df.columns:
            def ocultar_nombres(texto):
                if pd.isna(texto) or not texto.strip(): return texto
                nombres = [n.strip() for n in texto.split(',')]
                nombres_falsos = [mapa_funcionarios.get(n, n) for n in nombres]
                return ", ".join(nombres_falsos)
            
            df['Funcionario_Detectado'] = df['Funcionario_Detectado'].apply(ocultar_nombres)
            cambios += 1

        # E. Ocultar nombre del paciente (si existe)
        if 'nombre_paciente' in df.columns:
            df['nombre_paciente'] = "PACIENTE_CONFIDENCIAL"
        if 'paciente_clean' in df.columns:
             df['paciente_clean'] = "PACIENTE_CONFIDENCIAL"
        if 'reclamante_clean' in df.columns:
             df['reclamante_clean'] = "PACIENTE_CONFIDENCIAL"
             
        # Guardar archivo seguro
        if cambios > 0:
            df.to_parquet(OUTPUT_DIR / archivo, index=False)
            logging.info(f"Archivo seguro generado: {archivo}")

    print("-"*60)
    logging.info(f"ÉXITO: {len(mapa_ruts)} RUTs y {len(mapa_funcionarios)} Funcionarios anonimizados.")
    print("="*60)

if __name__ == "__main__":
    anonimizar_datos_reclamos()