import logging
import re
import unicodedata
import time
import os
from difflib import SequenceMatcher
from collections import defaultdict
from pathlib import Path
from typing import List, Tuple, Any

import pandas as pd


# CONFIGURACIÓN Y LOGGING

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

CORRECCIONES_MANUALES = {
    "noly": "noli", "yrene": "irene", "sra alba": "alba",
    "cinthia": "cynthia", "cintia": "cynthia"
}

TITULOS_MEDICOS = [
    "doctor", "doctora", "dr", "doc", "dra", "enfermero", "enfermera", "tens", "kine", 
    "kinesiologo", "kinesiologa", "matrona", "matron", "nutricionista", "tecnologo", 
    "tecnico", "auxiliar", "sr", "sra", "srta", "don", "doña", "tm"
]

MAPA_TITULOS_CARGOS = {
    'doc': ['doctor', 'medico', 'médico', 'cirujano'],
    'dr': ['doctor', 'medico', 'médico', 'cirujano'],
    'dra': ['doctora', 'medica', 'médica', 'cirujana', 'cirujano'],
    'doctor': ['doctor', 'medico', 'médico', 'cirujano'],
    'doctora': ['doctora', 'medica', 'médica', 'cirujana', 'cirujano'],
    'enfermera': ['enfermera', 'enfermeria'],
    'enfermero': ['enfermero', 'enfermeria'],
    'tens': ['tens', 'tecnico', 'técnico', 'paramedico'],
    'kine': ['kinesiologo', 'kinesiologa', 'kinesiología', 'kine'],
    'kinesiologo': ['kinesiologo', 'kinesiología', 'kine'],
    'kinesiologa': ['kinesiologa', 'kinesiología', 'kine'],
    'matrona': ['matron', 'matrona'],
    'matron': ['matron', 'matrona'],
    'tm': ['tecnologo', 'tecnólogo', 'tm'],
    'tecnologo': ['tecnologo', 'tecnólogo', 'tm']
}

VERBOS_CONTEXTO_PREVIO = [
    "debio", "debe", "deben", "debia", "debido",
    "solicito", "solicita", "solicitando",         
    "podria", "puede", "pueden", "podia"                 
]

# Lista completa
PALABRAS_PROHIBIDAS = [
    "que", "quien", "quienes", "el", "la", "los", "las", "un", "una", "unos", "unas", "muy", 
    "por", "para", "pero", "como", "cuando", "donde", "porque", "aunque", "sino", "solo",
    "entre", "segun", "tras", "mediante", "siempre", "nunca", "jamas", "quizas", "luego",
    "despues", "antes", "ademas", "incluso", "tambien", "tampoco", "entonces", "mientras",
    "del", "al", "se", "me", "te", "nos", "les", "le", "su", "sus", "mi", "mis", "tu", "con", "sin",
    "atiende", "atendio", "atendieron", "atendiendo", "atender", "atienden",
    "sale", "salio", "salir", "saliendo",
    "llega", "llego", "llegaron", "llegando", "llegar",
    "dice", "dijo", "dijeron", "diciendo", "decir",
    "fui", "fue", "fueron", "siendo", "ser",
    "tiene", "tenia", "tuvo", "tienen", "teniendo", "tener",
    "hace", "hizo", "hicieron", "haciendo", "hacer",
    "viene", "vino", "viniendo", "venir",
    "van", "iba", "yendo", "ir",
    "era", "estaba", "son", "soy", "estar",
    "informa", "informo", "informando", "informar",
    "especifico", "especifica", "especificando",
    "podria", "puede", "pueden", "pudiendo", "poder", "podia",
    "debe", "deben", "debio", "debido", "debiendo", "deber",
    "llaman", "llamando", "llamar", "llamo",
    "habia", "haber", "habiendo",
    "solicito", "solicita", "solicitando", "solicitar",
    "entregar", "entregando", "entrego",
    "procede", "procedio", "procediendo", "proceder",
    "consulte", "consulta", "consultando", "consultar",
    "acudio", "acude", "acudiendo", "acudir",
    "dejando", "dejar", "dejo",
    "viendo", "ver", "vio",
    "tomaron", "tomar", "tomando", "tomo",
    "emitir", "emitio", "emitiendo",
    "escribe", "escribio", "escribiendo", "escribir",
    "reconocio", "reconoce", "reconociendo", "reconocer",
    "senalo", "senala", "senalando", "senalar",
    "receto", "receta", "recetando", "recetar",
    "entrar", "entrando", "entro", "entraron",
    "excesiva", "excesivo", "tratante", "recien", "escribe", "ninguna", "ninguno", "algunos",
    "algunas", "presente", "antecedentes", "todos", "todas", "solo", "solamente", 
    "identificada", "simplemente", "estuvieran", "equipo", "preocupado", 
    "poco", "nada", "todo", "expertos", "experto", "atenta", "lesion",
    "emitido", "comportamiento", "considerar", "profesion",
    "senoritas", "senores", "caballeros", "damas",
    "auxiliares", "enfermeros", "medicos", "doctores",
    "delicados", "atentos", "amables", "profesionales",
    "area", "urgencias", "urgencia", "hospital", "clinica", "sala", "box", "piso",
    "atencion", "servicio", "departamento", "unidad", "sector",
    "enfermeria", "pabellon", "consulta", "consultorio",
    "camilla", "cama", "silla", "rinonera", "bandeja", "carro",
    "examen", "radiografia", "ecografia", "resonancia", "scanner",
    "medicamento", "remedio", "pastilla", "inyeccion", "suero",
    "bono", "pago", "cobro", "cuenta", "factura", "valor",
    "hora", "cita", "turno", "dia", "semana", "mes",
    "persona", "paciente", "familia", "acompanante",
    "problema", "caso", "situacion", "hecho", "evento",
    "vez", "ocasion", "momento", "instancia",
    "hoy", "ayer", "manana", "ahora",
    "dentro", "fuera", "arriba", "abajo", "cerca", "lejos", "aca", "alla",
    "bien", "mal", "mejor", "peor", "asi", "tal", "tan",
    "realmente", "visiblemente", "claramente", "obviamente", "evidentemente",
    "super", "fuerte", "mucho", "bastante", "demasiado",
    "buena", "bueno", "buenos", "buenas", "mala", "malo", "malos", "malas",
    "excelente", "excelentes", "pesada", "pesado", "pesados", "pesadas",
    "complicada", "complicado", "complicados", "complicadas",
    "pedante", "pedantes", "grosero", "grosera", "groseros", "groseras",
    "alta", "alto", "altos", "altas", "baja", "bajo", "bajos", "bajas",
    "gran", "grande", "grandes", "pequena", "pequeno", "pequenos", "pequenas",
    "primera", "primero", "primeros", "primeras", "segunda", "segundo", "tercera", "tercer",
    "nueva", "nuevo", "nuevos", "nuevas", "vieja", "viejo", "viejos", "viejas",
    "llevo", "llevas", "llevamos", "llevan", "llevado", "llegue", "llegado",
    "encuentra", "encontramos", "encuentran", "encontrado",
    "abrazo", "abrazas", "abrazamos", "abrazan", "abrazado",
    "regreso", "regresas", "regresamos", "regresan", "regresado",
    "retiro", "retiras", "retiramos", "retiran", "retirado",
    "observo", "observas", "observamos", "observan", "observado", "observaban",
    "propuso", "propone", "proponemos", "proponen", "propuesto",
    "grita", "gritas", "gritamos", "gritan", "gritado",
    "nombrada", "nombrado", "nombrados", "nombradas",
    "agacho", "agachas", "agachamos", "agachan", "agachado", "agachando", "acercandose",
    "habian", "hubiera", "habria", "hayan",
    "saca", "sacas", "sacamos", "sacan", "sacado",
    "explico", "explica", "explicamos", "explican", "explicado",
    "ordena", "ordenas", "ordenamos", "ordenan", "ordenado",
    "anota", "anotas", "anotamos", "anotan", "anotado", "anulando",
    "necesita", "necesitas", "necesitamos", "necesitan", "necesitado",
    "acaba", "acabas", "acabamos", "acaban", "acabado",
    "paso", "pasas", "pasamos", "pasan", "pasado",
    "visto", "vistas", "vistos", "vista",
    "emvia", "emvias", "emviamos", "emvian",
    "insiste", "insistes", "insistimos", "insisten", "insistido",
    "pregunto", "preguntas", "preguntamos", "preguntan", "preguntado",
    "destaco", "destacas", "destacamos", "destacan", "destacado",
    "sucedio", "sucede", "sucedemos", "suceden", "sucedido",
    "hacia", "hacias", "hacian",
    "contesta", "contestas", "contestamos", "contestan", "contestado",
    "cuido", "cuidas", "cuidamos", "cuidan", "cuidado",
    "trataron", "trata", "tratas", "tratamos", "tratan", "tratado", "trato",
    "reagendo", "reagenda", "reagendas", "reagendamos", "reagendan",
    "quedamos", "queda", "quedas", "quedan", "quedado",
    "estoy", "estas", "estamos", "estan", "estado", "estuvo", "estaban", "estuvieran",
    "valoro", "valoras", "valoramos", "valoran", "valorado",
    "limpiando", "limpia", "limpias", "limpiamos", "limpian", "limpiado",
    "confirmando", "confirma", "confirmas", "confirmamos", "confirman", "confirmado",
    "interrumpe", "interrumpes", "interrumpimos", "interrumpen", "interrumpido",
    "agredida", "agredido", "agredidos", "agredidas",
    "hostigamiento",
    "finalizacion", "inicio", "termino",
    "cual", "cuales", "cuyo", "cuya", "cuyos", "cuyas",
    "ambas", "ambos", "varios", "varias", "demas", "involucrados", "involucradas",
    "otro", "otra", "otros", "otras",
    "mismo", "misma", "mismos", "mismas",
    "ese", "esa", "esos", "esas", "aquel", "aquella", "aquellos", "aquellas",
    "ahi", "alla", "aca",
    "salud", "caracter", "forma", "ayuda", "feedback", "gracias", "muchas",
    "control", "revision", "avances", "atraso",
    "preguntas", "respuestas", "dudas", "consultas",
    "intento", "pregunta",
    "llegada", "salida", "espera", "esperanza",
    "cargo", "modulo", "recepcion", "meson",
    "tono", "voz",
    "guante", "guantes",
    "anamnesis", "abdomen", "mamaria",
    "medicamentos", "curaciones",
    "puerta", "abierta", "cerrada",
    "rato", "veces",
    "dicha", "dicho",
    "secretaria", "secretario",
    "ejecutiva", "ejecutivo",
    "call", "center",
    "lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo",
    "preferencial",
    "traumatologia",
    "hombro", "columna", "rodilla", "brazo",
    "medicina", "general",
    "personal",
    "supervisora", "supervisor",
    "universitaria",
    "aseo",
    "abdomen", "tac",
    "modulo",
    "verbalmente",
    "agradecidos", "felicitaciones",
    "mejores",
    "tremenda",
    "durante",
    "neurocirujano", "gastroenterologo", "urologo", "cirujano", "pediatra",
    "kinesiologos"
]

PALABRAS_CRITICAS = {
    'Riesgo Legal / Ético': {
        'palabras': ['negligencia', 'mala praxis', 'fallecio', 'muerte', 'riesgo vital', 'secuela', 
                     'tumor', 'superintendencia', 'demanda', 'ley 20', '20.584', 'derechos', 
                     'vulneracion', 'acciones legales', 'denuncia', 'sumario', 'abogado', 
                     'indemnizacion', 'minsal', 'sernac', 'perjuicios'],
        'peso': 5.0
    },
    'Maltrato Grave': {
        'palabras': ['grosero', 'grito', 'gritos', 'prepotente', 'altanero', 'maltrato', 'desprecio', 
                     'fuerza desmedida', 'dano psicologico', 'dano emocional', 'humillante', 'burlesco', 
                     'vejatorio', 'agresion', 'agredida', 'llorando', 'llorar', 'amenaza', 'amenazo'],
        'peso': 4.0
    },
    'Error Clínico Severo': {
        'palabras': ['error medico', 'abandono', 'sin atencion', 'infeccion', 'quemadura', 'reingreso', 
                     'codigo azul', 'paro cardiaco', 'contaminada', 'equivocado', 'equivoco', 'grave'],
        'peso': 3.0
    },
    'Fricción Operacional': {
        'palabras': ['espera excesiva', 'retraso', 'nunca llego', 'cancelada'],
        'peso': 1.0
    },
    'Financiero': {
        'palabras': ['cobro indebido', 'devolucion', 'doble cobro', 'pagare'],
        'peso': 0.5
    }
}

PESOS_CATEGORIA = {
    'Atención clínica': 1.4, 'Hospitalización': 1.3, 'Admisión': 1.1,
    'Gestión de cuentas y cobros': 1.0, 'Servicios generales - Infraestructura': 0.9, 'Otros': 0.8
}

MAPA_AREAS = {
    'Colonoscopía / Endoscopía': 'Endoscopia / Colonoscopia', 
    'Urgencias': 'Urgencias',
    'Hospitalizados - Médico Quirúrgico': 'Hospitalizados', 
    'UPC (UTI, UCI)': 'UPC',
    'Imagenología': 'Imagenología', 
    'Laboratorio / Toma de muestras': 'Laboratorio'
}


# FUNCIONES AUXILIARES DE TRANSFORMACIÓN Y NLP

def similitud_fuzzy(a: str, b: str) -> float:
    """Calcula similitud entre dos strings (0-1)"""
    if not a or not b: 
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def encontrar_mejor_match(texto: str, candidatos: List[str], umbral: float = 0.75) -> Tuple[Any, float]:
    """Encuentra el mejor match fuzzy entre texto y lista de candidatos."""
    mejor_match, mejor_score = None, 0.0
    for candidato in candidatos:
        score = similitud_fuzzy(texto, candidato)
        if score > mejor_score:
            mejor_score = score
            mejor_match = candidato
    
    if mejor_score >= umbral:
        return mejor_match, mejor_score
    return None, 0.0

def limpiar_texto(texto: Any) -> str:
    """Normalización de texto"""
    if pd.isna(texto): 
        return ""
    texto = str(texto).lower().strip()
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    texto = re.sub(r'[^\w\s]', ' ', texto)
    texto = re.sub(r'\bdr\b', 'doctor', texto)
    texto = re.sub(r'\bdra\b', 'doctora', texto)
    texto = re.sub(r'\bsta\b', 'srta', texto)
    
    for error, correccion in CORRECCIONES_MANUALES.items():
        if error in texto: 
            texto = texto.replace(error, correccion)
    return texto

def validar_integridad_columnas(df: pd.DataFrame) -> pd.DataFrame:
    """
    Control de calidad de datos
    Evita que valores de descripciones largas se filtren y contaminen
    columnas categóricas como ORIGEN o NOMPREVI debido a desajustes en el input.
    """
    if 'ORIGEN' in df.columns:
        df['ORIGEN'] = df['ORIGEN'].apply(lambda x: x if pd.isna(x) or len(str(x)) < 30 else None)
    if 'NOMPREVI' in df.columns:
        df['NOMPREVI'] = df['NOMPREVI'].apply(lambda x: x if pd.isna(x) or len(str(x)) < 40 else None)
    return df

def validar_contexto_verbal(texto_completo: str, posicion_match: int, nombre_extraido: str) -> bool:
    """Valida que el nombre detectado no esté siendo utilizado como verbo en el contexto"""
    palabras = texto_completo.split()
    try:
        idx_match = palabras.index(nombre_extraido.split()[0], max(0, posicion_match - 20))
    except ValueError:
        return True 
    
    ventana_previa = palabras[max(0, idx_match - 3):idx_match]
    
    if any(verbo in ventana_previa for verbo in VERBOS_CONTEXTO_PREVIO):
        return False
    
    if len(nombre_extraido.split()) == 1 and nombre_extraido.lower() in PALABRAS_PROHIBIDAS:
        return False
        
    return True

def es_nombre_valido(nombre: str) -> bool:
    """Reglas  para descartar falsos positivos en nombres propios"""
    if not nombre or len(nombre) < 3:
        return False
    
    palabras = nombre.lower().split()
    for palabra in palabras:
        if len(palabra) < 3 or palabra in PALABRAS_PROHIBIDAS: 
            return False
    return True

def calcular_isr_inteligente(row: pd.Series) -> float:
    """Calcula el Índice de Severidad de Reclamo (ISR) en una escala de 0 a 1"""
    tipo_reclamo = str(row.get('tipoReclamo', '')).strip().lower()
    texto_norm = limpiar_texto(row.get('hechosReclamo', ''))
    
    if 'felicitaci' in tipo_reclamo or 'felicit' in texto_norm or 'agradec' in texto_norm: 
        return 0.0
        
    gatillos_criticos = ['superintendencia', 'ley 20', 'abogado', 'negligencia', 'acciones legales', 'indemnizacion']
    if any(palabra in texto_norm for palabra in gatillos_criticos):
        return 1.0
    
    score_palabras = sum(data['peso'] for cat, data in PALABRAS_CRITICAS.items() 
                         for p in data['palabras'] if limpiar_texto(p) in texto_norm)
    
    if score_palabras >= 5.0:
        return min(1.0, 0.85 + (score_palabras * 0.01))
        
    score_palabras_norm = min(1.0, score_palabras / 10.0)
    largo_norm = min(1.0, len(str(row.get('hechosReclamo', ''))) / 1000)
    
    val_atrasado = str(row.get('atrasado', '')).lower()
    val_dias = row.get('diasRestantes', 0)
    atraso_norm = min(1.0, abs(float(val_dias)) / 10) if val_atrasado == 'si' and pd.notna(val_dias) else 0.0
    
    val_monto = row.get('montoDevolucion', 0)
    monto_norm = min(1.0, float(val_monto) / 500000) if pd.notna(val_monto) and float(val_monto) > 0 else 0.0
    
    peso_cat = PESOS_CATEGORIA.get(row.get('Categoría', 'Otros'), 1.0)
    
    isr_final = (0.35 * score_palabras_norm) + (0.15 * largo_norm) + (0.20 * atraso_norm) + (0.15 * monto_norm) + (0.15 * (peso_cat / 1.4))
    
    if 'sugerencia' in tipo_reclamo: 
        isr_final *= 0.5
        
    return min(1.0, isr_final)


# PIPELINE PRINCIPAL DE PROCESAMIENTO

def procesar_expedientes(ruta_expedientes: Path, ruta_nomina: Path, ruta_externos: Path, ruta_salida: Path, ruta_auditoria: Path):
    """Orquesta la carga, análisis NLP y almacenamiento de los datos"""
    start_time = time.time()
    logging.info("Iniciando Motor NLP (Extracción de Entidades y Riesgo)")
    
    try:
        df_fel = pd.read_excel(ruta_expedientes)
        df_staff = pd.read_excel(ruta_nomina)
        logging.info(f"Datos base cargados: {len(df_fel)} expedientes, {len(df_staff)} funcionarios internos.")
    except Exception as e:
        logging.error(f"Error crítico al cargar expedientes o nómina base: {e}")
        return

    # Carga de nómina externa (Opcional y a prueba de fallos)
    try:
        df_ext = pd.read_excel(ruta_externos)
        col_ext_nombre = df_ext.columns[0]
        col_ext_cargo = df_ext.columns[2]
        logging.info(f"Nómina externa cargada: {len(df_ext)} registros.")
    except FileNotFoundError:
        logging.warning(f"Falta el archivo {ruta_externos.name}. El motor continuará solo con la nómina interna.")
        df_ext = pd.DataFrame(columns=['Nombre', 'RUT', 'Cargo'])
        col_ext_nombre = 'Nombre'
        col_ext_cargo = 'Cargo'

    # 2. PREPARACIÓN DE DICCIONARIOS DE IDENTIDAD
    df_staff['nombre_clean'] = df_staff['NOMBRE'].apply(limpiar_texto)
    df_staff['apellido_p_clean'] = df_staff['APELLIDO'].apply(limpiar_texto)
    df_staff['nombre_completo'] = df_staff['NOMBRE'] + " " + df_staff['APELLIDO']
    
    indice_nombres = defaultdict(list)
    for _, emp in df_staff.iterrows():
        nombre_completo_clean = limpiar_texto(emp['nombre_completo'])
        
        base_dict = {
            'cargo': emp['CARGO'], 
            'area': emp.get('NOMBRE ÁREA', '')
        }
        
        indice_nombres['completo'].append({**base_dict, 'original': emp['nombre_completo'].title(), 'clean': nombre_completo_clean})
        
        if emp['nombre_clean'] and emp['apellido_p_clean']:
            indice_nombres['nombre_apellido'].append({
                **base_dict,
                'original': f"{emp['NOMBRE']} {emp['APELLIDO']}".title(), 
                'clean': f"{emp['nombre_clean']} {emp['apellido_p_clean']}"
            })

    indice_externos = []
    for _, emp in df_ext.iterrows():
        nombre_ext = str(emp[col_ext_nombre])
        cargo_ext = str(emp[col_ext_cargo])
        if pd.notna(nombre_ext) and str(nombre_ext).strip() != 'nan':
            indice_externos.append({
                'original': nombre_ext.strip().title(),
                'clean': limpiar_texto(nombre_ext),
                'cargo': cargo_ext.strip(),
                'cargo_clean': limpiar_texto(cargo_ext)
            })

    # 3. EXTRACCIÓN NLP Y CÁLCULO DE RIESGO
    df_fel['paciente_clean'] = df_fel.get('nombrePaciente', '').apply(limpiar_texto)
    df_fel['reclamante_clean'] = df_fel.get('nombreReclamante', '').apply(limpiar_texto)
    
    resultados_nombres, resultados_cargos, isr_scores, auditoria_casos = [], [], [], []
    patron_extraccion = r'\b(' + '|'.join(TITULOS_MEDICOS) + r')\s+([a-záéíóúñ]+(?:\s+[a-záéíóúñ]+){0,3})'
    
    logging.info("Analizando textos libres y calculando ISR...")
    
    for index, row in df_fel.iterrows():
        texto_crudo = str(row.get('hechosReclamo', ''))
        texto_clean = limpiar_texto(texto_crudo)
        isr_scores.append(calcular_isr_inteligente(row))
        
        unidad_reclamo = row.get('unidadResponsable', '')
        area_destino = MAPA_AREAS.get(unidad_reclamo, 'TODAS')
        
        finalistas = set()
        finalistas_cargos = set()
        estrategias_usadas = []
        
        # Estrategia: Búsqueda Exacta
        for candidato in indice_nombres['completo'] + indice_nombres['nombre_apellido']:
            if area_destino != 'TODAS' and candidato.get('area', '') != area_destino: 
                continue
            if (candidato['clean'] in texto_clean and 
                candidato['clean'] not in row['paciente_clean'] and 
                candidato['clean'] not in row['reclamante_clean']):
                finalistas.add(candidato['original'])
                finalistas_cargos.add(candidato['cargo'])
                estrategias_usadas.append('MATCH_EXACTO_INTERNO')

        # Validación de contexto
        if not finalistas:
            matches = re.finditer(patron_extraccion, texto_clean, re.IGNORECASE)
            for match in matches:
                titulo = match.group(1).lower()
                palabras_potenciales = match.group(2).split()
                posicion = match.start()
                
                nombre_limpio = [p for p in palabras_potenciales if p not in PALABRAS_PROHIBIDAS and len(p) > 2]
                if not nombre_limpio: 
                    continue
                    
                nombre_extraido = " ".join(nombre_limpio[:3])
                
                if (nombre_extraido in row['paciente_clean'] or 
                    nombre_extraido in row['reclamante_clean'] or
                    not es_nombre_valido(nombre_extraido) or
                    not validar_contexto_verbal(texto_clean, posicion, nombre_extraido)):
                    continue
                
                candidatos_completos = [c['clean'] for c in indice_nombres['completo'] 
                                        if area_destino == 'TODAS' or c['area'] == area_destino]
                mejor_match, score = encontrar_mejor_match(nombre_extraido, candidatos_completos, umbral=0.70)
                
                # A) Intentar Fuzzy Interno
                if mejor_match:
                    for c in indice_nombres['completo']:
                        if c['clean'] == mejor_match:
                            finalistas.add(c['original'])
                            finalistas_cargos.add(c['cargo'])
                            estrategias_usadas.append(f'FUZZY_INTERNO_{score:.2f}')
                            break
                    continue  # Si lo encontró en planta, pasamos al siguiente nombre
                
                # B) Triangulación con Externos y Honorarios (Restaurado)
                terminos_cargo = MAPA_TITULOS_CARGOS.get(titulo, [titulo])
                coincidencias_externas = []
                
                for ext in indice_externos:
                    if nombre_extraido in ext['clean']:
                        if any(term in ext['cargo_clean'] for term in terminos_cargo):
                            coincidencias_externas.append(ext)
                
                if len(coincidencias_externas) == 1:
                    ext_elegido = coincidencias_externas[0]
                    nombre_orig = ext_elegido['original']
                    if len(nombre_orig.split()) >= 2:
                        finalistas.add(nombre_orig.title())
                    else:
                        finalistas.add(f"{titulo.capitalize()} {nombre_orig.title()}")
                    finalistas_cargos.add(ext_elegido['cargo'])
                    estrategias_usadas.append('TRIANGULADO_EXTERNO_EXACTO')
                elif len(coincidencias_externas) == 0:
                    # El salvavidas original: Nombres válidos que no están en ninguna base (Médicos honorarios)
                    if len(nombre_extraido.split()) >= 2:
                        finalistas.add(nombre_extraido.title())
                        finalistas_cargos.add("Honorario / Externo")
                        estrategias_usadas.append('EXTERNO_GENERICO_VALIDADO')

        nombres_str = ", ".join(list(finalistas)) if finalistas else ""
        cargos_str = ", ".join(list(finalistas_cargos)) if finalistas_cargos else ""
        
        resultados_nombres.append(nombres_str)
        resultados_cargos.append(cargos_str)
        
        auditoria_casos.append({
            'Folio': row.get('folioInterno', ''), 
            'Unidad': unidad_reclamo,
            'Detectados': nombres_str, 
            'Cargo': cargos_str,
            'Estrategia': ', '.join(estrategias_usadas) if estrategias_usadas else 'NINGUNA',
            'Texto': texto_crudo[:200]
        })
        
        if (index + 1) % 100 == 0: 
            logging.info(f"Procesados: {index + 1}/{len(df_fel)} expedientes...")

    df_fel['ISR_Calculado'] = isr_scores
    df_fel['Funcionario_Detectado'] = resultados_nombres
    df_fel['Cargo_Detectado'] = resultados_cargos
    
    # 4. EXPORTACIÓN A STAGING
    logging.info("Guardando resultados en área de Staging...")
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df_fel.to_excel(ruta_salida, index=False)
    pd.DataFrame(auditoria_casos).to_excel(ruta_auditoria, index=False)

    con_funcionarios = (df_fel['Funcionario_Detectado'] != '').sum()
    tasa_deteccion = (con_funcionarios / len(df_fel)) * 100
    
    logging.info(f"PROCESAMIENTO COMPLETADO en {time.time() - start_time:.2f}s")
    logging.info(f"Expedientes CON funcionarios: {con_funcionarios:,} ({tasa_deteccion:.1f}%)")
    logging.info(f"Expedientes SIN funcionarios: {len(df_fel) - con_funcionarios:,} ({100 - tasa_deteccion:.1f}%)")
    logging.info(f"Salida principal: {ruta_salida}")

if __name__ == "__main__":
    # Agregamos un .parent extra para llegar a la raíz del repositorio
    BASE_DIR = Path(__file__).resolve().parent.parent.parent
    
    procesar_expedientes(
        ruta_expedientes=BASE_DIR / "data/expedientes.xlsx", 
        ruta_nomina=BASE_DIR / "data/nomina_funcionarios.xlsx", 
        ruta_externos=BASE_DIR / "data/externos_nomina.xlsx", 
        ruta_salida=BASE_DIR / "staging/expedientes_enriquecidos.xlsx",
        ruta_auditoria=BASE_DIR / "staging/auditoria_deteccion.xlsx"
    )