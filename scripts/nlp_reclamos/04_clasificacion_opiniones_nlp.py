import logging
import re
from pathlib import Path
from collections import Counter
from itertools import combinations
from typing import Dict, Any, List

import pandas as pd


# CONFIGURACIÓN Y LOGGING
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


# DICCIONARIO DE TAXONOMÍA (CONSTANTES CLÍNICAS Y OPERACIONALES)
CATEGORIAS_KEYWORDS = {
    "Riesgo Clínico": [
        'negligen', 'mala praxis', 'diagnostico erroneo', 'diagnostico incorrecto',
        'infeccion', 'demanda', 'abogado', 'superintendencia', 'sernac',
        'secuela', 'falleci', 'muerte', 'error medico', 'mal procedimiento',
        'fraude', 'sinverguenza'
    ],
    "Atención Médica": [
        'doctor', 'doctora', 'medico', 'medica', 'dr ', 'dra ',
        'pediatra', 'ginecologo', 'traumatologo', 'cardiologo',
        'cirujano', 'especialista', 'profesional medico',
        'atencion medica', 'consulta medica', 'diagnostico', 'tratamiento',
        'receta', 'examenes medicos', 'profesionales de vocacion'
    ],
    "Enfermería y TENS": [
        'enfermera', 'enfermero', 'tens', 'paramedico', 'toma de muestra',
        'inyeccion', 'curacion', 'suero', 'vacuna', 'personal de medicina preventiva'
    ],
    "Urgencias / Hospitalización": [
        'urgencia', 'emergencia', 'hospital', 'cama', 'internado',
        'hospitalizacion', 'uci', 'uti', 'pabellon', 'cirugia',
        'operacion', 'operaron', 'operar', 'vesicula',
        'codigo azul', 'reanimacion', 'equipo medico'
    ],
    "Recepción y Personal Administrativo": [
        'secretari', 'recepcion', 'admision', 'meson', 'cajer',
        'informaciones', 'modulo', 'personal administrativo',
        'ejecutiva', 'ejecutivo', 'guardia', 'guardias',
        'señaletica', 'señalizacion', 'entrada', 'acceso',
        'agendar', 'reagendar', 'cita', 'reserva'
    ],
    "Tiempos de Espera y Rapidez": [
        'espera', 'demora', 'atras', 'puntualidad',
        'tarde', 'lento', 'lentisim', 'rapidez', 'agilidad',
        'rapida atencion', 'atencion rapida',
        'perder tiempo', 'perdiendo tiempo',
        'hora', 'minuto', 'retraso', 'cancelada'
    ],
    "Call Center y Contacto Telefónico": [
        'telefon', 'call center', 'operadora', 'whatsapp', 'correo', 'email',
        'anexo ', 'extension ', 'grabadora', 'linea ocupada',
        'contestan', 'atienden el telefono', 'mesa central'
    ],
    "Laboratorio e Imagenología": [
        'laboratorio', 'examen', 'resultado', 'informe', 'muestra',
        'sangre', 'orina', 'radiografia', 'ecografia', 'scanner',
        'resonancia', 'mamografia', 'imagen', 'test', 'antigeno'
    ],
    "Entrega de Resultados": [
        'resultado', 'informe', 'entrega', 'portal', 'clave',
        'descargar', 'online', 'plataforma', 'sistema online',
        'me estuvieron mandando', 'buscando examenes'
    ],
    "Cobros y Facturación": [
        'bono', 'pagar', 'pago', 'cobro', 'factura', 'devolucion',
        'reembolso', 'precio', 'costo', 'valor', 'caro',
        'convenio', 'isapre', 'fonasa', 'cotizar', 'presupuesto',
        'plata que te cobran'
    ],
    "Farmacia": [
        'farmacia', 'medicamento', 'remedio', 'bota inmovilizadora'
    ],
    "Infraestructura y Aseo": [
        'baño', 'ascensor', 'estacionamiento', 'parking',
        'suci', 'olor', 'aseo', 'limpieza', 'papel',
        'climatizacion', 'calor', 'frio', 'silla', 'comodo',
        'edificio', 'instalaciones', 'limpio', 'ordenado',
        'amplio', 'espacioso', 'acogedor',
        'mantenido', 'higiene', 'sanidad'
    ],
    "Sistemas y Tecnología": [
        'totem', 'sistema', 'huellero', 'transbank', 'redcompra',
        'sistema caido', 'falla tecnica', 'no funciona',
        'computadora', 'word pirata'
    ],
}


# CLASIFICACIÓN NLP

def clasificacion_fallback(texto: str) -> Dict[str, int]:
    """Sistema heurístico secundario para capturar intenciones complejas o sentimientos puros"""
    texto_limpio = str(texto).lower()
    
    patrones_contacto = [
        r'\bno\s+(atienden|contestan|responde)',
        r'imposible\s+(comunicar|ubicar)',
        r'(telefon|llamar|contactar).*?(no|nunca|jamas)',
        r'grabadora', r'menu.*?menu'
    ]
    if any(re.search(p, texto_limpio) for p in patrones_contacto):
        return {"Call Center y Contacto Telefónico": 3}
        
    patrones_velocidad = [
        r'\b(rapida|rapido|pronto)\s+(atencion|servicio)',
        r'(atencion|servicio)\s+(rapida|rapido)',
        r'\b(lento|demora|tarde|espera)', r'perdi(endo|ó)\s+tiempo'
    ]
    if any(re.search(p, texto_limpio) for p in patrones_velocidad):
        return {"Tiempos de Espera y Rapidez": 2}
        
    patrones_personal = [
        r'(ejecutiva|ejecutivo|guardia|secretaria)s?\s+(atiende|mal|pesima|no sabe)',
        r'(recepcion|mesa\s+central|modulo).*?(mal|pesima|horrible)', r'pasarse\s+la\s+pelota'
    ]
    if any(re.search(p, texto_limpio) for p in patrones_personal):
        return {"Recepción y Personal Administrativo": 2}
        
    if re.search(r'\batencion\b', texto_limpio):
        calificativos = ['buena', 'excelente', 'muy bien', 'maravillosa', 'rapida', 
                         'mala', 'pesima', 'horrible', 'deficiente', 'lenta']
        if any(cal in texto_limpio for cal in calificativos):
            if re.search(r'\b(medico|profesional|doctor)', texto_limpio):
                return {"Atención Médica": 1}
            return {"Calidad de Atención General": 1}
            
    palabras_sentimiento = ['basura', 'asco', 'penca', 'horrible', 'pesimo']
    if any(p in texto_limpio for p in palabras_sentimiento):
        if len(texto_limpio) < 30:
            return {"Sin Detalles": 1}
            
    return {"Otro / No Clasificado": 1}

def clasificar_opinion(texto: Any) -> Dict[str, int]:
    """Clasificación multi-etiqueta basado en frecuencias."""
    if pd.isna(texto) or str(texto).strip() == "" or str(texto).lower() in ["sin comentarios", "sin texto"]:
        return {"8. Sin Texto": 1}
        
    texto_limpio = str(texto).lower()
    scores = {}
    
    for categoria, palabras in CATEGORIAS_KEYWORDS.items():
        matches = sum(len(re.findall(r'\b' + palabra, texto_limpio)) for palabra in palabras)
        if matches > 0:
            scores[categoria] = matches
            
    if not scores:
        scores = clasificacion_fallback(texto)
        
    return scores

def calcular_sentimiento_ponderado(nota: float, num_categorias: int, categoria_principal: str) -> str:
    """Clasifica el sentimiento combinando el NPS del usuario y la complejidad del reclamo."""
    if pd.isna(nota):
        return 'Sin Calificación'
        
    if categoria_principal == "8. Sin Texto":
        if nota <= 2: return 'Rojo (Detractor Sin Detalles)'
        elif nota >= 4: return 'Verde (Promotor Sin Detalles)'
        else: return 'Amarillo (Pasivo Sin Detalles)'
        
    if nota <= 2:
        if num_categorias >= 3: return 'Rojo Crítico (Multi-Problema)'
        return 'Rojo (Detractor)'
    elif nota == 3:
        return 'Amarillo (Pasivo)'
    else:
        return 'Verde (Promotor)'


# ANÁLISIS DE CAUSA RAÍZ - CORRELACIONES

def generar_matriz_correlaciones(df: pd.DataFrame) -> pd.DataFrame:
    """Extrae las combinaciones de problemas que ocurren simultáneamente en una reseña."""
    datos_correlaciones = []
    
    for _, row in df.iterrows():
        cats = list(row['Categorias_Detectadas'].keys())
        if len(cats) >= 2:
            for par in combinations(cats, 2):
                par_ordenado = sorted(par)
                datos_correlaciones.append({
                    'Problema_A': par_ordenado[0],
                    'Problema_B': par_ordenado[1],
                    'Cruce': f"{par_ordenado[0]} + {par_ordenado[1]}",
                    'fecha_opinion': row['fecha_opinion'],
                    'nota_google': row['nota_google'],
                    'Sentimiento': row['Sentimiento']
                })
                
    return pd.DataFrame(datos_correlaciones)


# ORQUESTADOR PRINCIPAL

def ejecutar_pipeline_clasificacion(ruta_entrada: Path, dir_salida: Path):
    logging.info("Iniciando Clasificación NLP")
    
    if not ruta_entrada.exists():
        logging.error(f"No se encontró el archivo de entrada: {ruta_entrada}")
        return

    df = pd.read_parquet(ruta_entrada)
    
    # 1. Aplicar Clasificación y Sentimiento
    logging.info("Clasificando reseñas...")
    df['Categorias_Detectadas'] = df['texto_opinion'].apply(clasificar_opinion)
    df['Categoria_Principal'] = df['Categorias_Detectadas'].apply(lambda x: max(x, key=x.get) if x else "9. Otro")
    df['Score_Principal'] = df['Categorias_Detectadas'].apply(lambda x: max(x.values()) if x else 0)
    df['Num_Categorias'] = df['Categorias_Detectadas'].apply(len)
    df['Sentimiento'] = df.apply(lambda row: calcular_sentimiento_ponderado(row['nota_google'], row['Num_Categorias'], row['Categoria_Principal']), axis=1)
    
    # 2. Generar Correlaciones
    logging.info("Generando matriz de correlación de problemas...")
    df_correlaciones = generar_matriz_correlaciones(df)
    
    # 3. Exportar Datos
    dir_salida.mkdir(parents=True, exist_ok=True)
    ruta_enriquecido = dir_salida / 'google_reviews_enriquecido.parquet'
    ruta_correlaciones = dir_salida / 'google_correlaciones_detalle.parquet'
    
    df.to_parquet(ruta_enriquecido, index=False)
    if not df_correlaciones.empty:
        df_correlaciones.to_parquet(ruta_correlaciones, index=False)
        
    # 4. Reporte de Ejecución
    logging.info("RESUMEN DE CLASIFICACIÓN")
    resumen = df['Categoria_Principal'].value_counts()
    for cat, count in resumen.items():
        logging.info(f"{cat:40s}: {count:3d} ({(count/len(df))*100:5.1f}%)")
        
    if not df_correlaciones.empty:
        logging.info("TOP 3 CRUCES DE PROBLEMAS")
        top_cruces = df_correlaciones['Cruce'].value_counts().head(3)
        for cruce, count in top_cruces.items():
            logging.info(f"{count:3d}x -> {cruce}")

    logging.info(f"Tablas maestras generadas exitosamente en {dir_salida}/")

if __name__ == "__main__":
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    ejecutar_pipeline_clasificacion(
        ruta_entrada=BASE_DIR / 'output/google_reviews.parquet',
        dir_salida=BASE_DIR / 'output'
    )