## Proyecto 1: Sistema de Inteligencia de Clientes, NLP y Triage de Riesgo Operacional

> **Nota de Seguridad:**
> En cumplimiento con la normativa de protección de datos (Ley N° 20.584 y estándares de confidencialidad médica):
> 1. Se eliminaron todos los nombres reales del personal clínico y administrativo, sustituyéndolos por identificadores sintéticos secuenciales (`Funcionario_001`, `Funcionario_002`, etc.).
> 2. Los RUTs de pacientes fueron generados aleatoriamente resguardando la integridad referencial para los cruces entre sistemas (CASS y MasterKey).
> 3. Las métricas cuantitativas y volúmenes sufrieron una perturbación estocástica de ($\pm 5\% - 10\%$) para evitar la exposición de datos financieros/operativos sensibles sin alterar las tendencias ni las conclusiones del modelo.
> 4. **Nota sobre el desarrollo del código:** La elaboración avanzada y optimización de los scripts en Python y SQL (DuckDB) contó con el apoyo de herramientas de Inteligencia Artificial, utilizadas como asistente para la estructuración de algoritmos de fuzzy matching, expresiones regulares.

---

### Contexto Operativo y Problema de Negocio
Las instituciones de salud reciben diariamente cientos de interacciones cualitativas a través de canales formales (sistema de reclamos/expedientes) e informales (encuestas CASS/Fibra y Google Reviews). Históricamente, el procesamiento de esta "Voz del Cliente" sufría de tres cuellos de botella:
1. **Falta de Priorización por Riesgo:** Todos los reclamos se trataban por igual en una cola FIFO, ignorando eventos centinela o de riesgo legal/ético crítico.
2. **Sesgo de Volumen Bruto:** Las unidades de alto flujo (ej. Consulta Médica) registraban mayor cantidad de quejas simplemente por atender a más pacientes, castigando injustamente su gestión frente a áreas pequeñas.
3. **Desconexión Transaccional:** Imposibilidad de vincular el relato cualitativo con la actividad operacional real del paciente en el Core Hospitalario.

---

### Definiciones de Métricas e Indicadores de Riesgo

Para normalizar la comparación entre unidades de distinto tamaño y severidad, se diseñó e implementó los siguientes indicadores:

* **1. ISR (Índice de Severidad del Reclamo - $ISR \in [0, 1]$):**
    Mide la *gravedad o impacto legal/clínico/emocional* del texto. Un algoritmo de NLP escanea palabras clave críticas (ej. *"Superintendencia"*, *"Negligencia"*, *"Gritos"*, *"Cobro indebido"*) y calcula un puntaje ponderado. Un $ISR = 1.0$ representa un evento crítico de alto riesgo legal.
* **2. TRU (Tasa de Riesgo Unificado):**
    Normaliza la *frecuencia* de reclamos en función del volumen operado. Evita el sesgo de comparar unidades masivas contra unidades pequeñas:
    $$\text{TRU} = \frac{\text{Total de Expedientes}}{\text{Total de Atenciones (Pacientes Únicos)}} \times 1.000$$
    *Nota técnica:* Se distingue entre **Atenciones** (pacientes únicos que ingresan a la unidad) y **Actividades** (volumen de insumos/procedimientos ejecutados, ej. jeringas, exámenes), demostrando matemáticamente la densidad operativa.
* **3. IRU Riesgo (Índice de Riesgo Unificado):**
    Ponderación matricial entre la *frecuencia relativa* (TRU) y el *impacto/severidad promedio* ($ISR$). Identifica la prioridad real de intervención gerencial:
    $$\text{IRU Riesgo} = \text{TRU} \times \overline{ISR}$$

---

### Interfaz Ejecutiva de Control (Estructura del Dashboard en Power BI)

![Página 1 - Vista Principal](assets/nlp_reclamos/01pagina1.png)

<details>
  <summary> <b>Despliega aquí para explorar el desglose de las 6 Páginas del Dashboard</b></summary>

#### Página 1: Matriz Corporativa de Riesgo y Evaluación CASS
* **Matriz de Riesgo Unificado (Scatter Plot):** Gráfico de dispersión cruzando la **TRU** vs. **ISR Promedio**, donde cada burbuja representa una unidad clínica. Permite identificar de un vistazo las áreas en la "Zona Roja" (alta tasa de reclamo y alta severidad).
* **Evaluación de Satisfacción CASS:** Distribución cualitativa de las preguntas de la encuesta de experiencia y su correspondiente nota/calificación (NPS)
* **Monitores de Volumen:** Tarjetas y gráficos de distribución de `Total Expedientes por Funcionario` y `Total Expedientes por Unidad`.

![Página 1 - Matriz Corporativa de Riesgo y Evaluación CASS](assets/nlp_reclamos/01pagina1.png)

#### Página 2: Responsabilidad de Funcionarios y Subcategorías
* **Tabla de Gestión por Unidad:** Cuadro consolidado mostrando `Unidad`, `TRU`, `ISR Promedio` e `IRU Riesgo`.
* **Tabla de Responsabilidad de Personal:** Desglose individual de `Funcionario` (ej. *Funcionario_014*), `Unidad`, `Total Expedientes asociados` e `ISR Promedio`.
* **Análisis Causal:** Gráfico de barras de `Total Expedientes por Subcategoría` (Trato, Tiempos de Espera, Facturación, etc.).

![Página 2 - Responsabilidad de Funcionarios y Subcategorías](assets/nlp_reclamos/02pagina2.png)

#### Página 3: Normalización por Carga Operativa y Mapeo Temporal
* **Capacidad vs. Reclamos (Gráfico Combinado):** Columnas que comparan `Total Atenciones` (pacientes) vs. `Total Actividades` (insumos/prestaciones) por Unidad, superpuestas con una línea de `Total Expedientes`. 
    * *Justificación de Negocio:* Explica visualmente por qué Consulta Médica genera más quejas brutas (alto volumen de atenciones) y permite justificar la necesidad del indicador TRU.
* **Heatmap Temporal (Unidad vs. Día de la Semana):** Mapa de calor que expone los peaks operativos de quejas (ej. demostrando que *Consulta Médica* colapsa los días *Lunes*).
* **Evolución Severidad vs. Volumen (Gráfico de Doble Eje):** Línea de tiempo (`Fecha` vs. `IRU Riesgo` y `Total Expedientes`) para auditar días donde, con pocos reclamos, el riesgo fue críticamente alto.

![Página 3 - Normalización por Carga Operativa y Mapeo Temporal](assets/nlp_reclamos/03pagina3.png)

#### Página 4: Triangulación CASS vs. Expedientes (Cruce de RUTs)
* **Propósito:** Cruce transaccional en DuckDB que identifica pacientes que hicieron un reclamo formal y que, a su vez, contestaron la encuesta de satisfacción CASS.
* **Caso 1 (Misma Unidad):** El problema reportado en el reclamo coincide con la unidad evaluada en CASS (falla acotada y localizada).
* **Caso 2 (Contagio Inter-Unidad):** El reclamo pertenece a una unidad (ej. *Admisión*), pero la mala nota en CASS se reflejó en otra (ej. *Consulta Médica*). Evidencia el "efecto contagio" en la percepción del paciente.

![Página 4 - Triangulación CASS vs Expedientes](assets/nlp_reclamos/04pagina4.png)

#### Página 5: Análisis de Sentimiento en Google Reviews
* Categorización y conteo de reseñas públicas de Google mediante NLP, clasificadas por categoría principal de servicio y sentimiento.

![Página 5 - Análisis de Sentimiento en Google Reviews](assets/nlp_reclamos/05pagina5.png)

#### Página 6: Co-Ocurrencia de Problemas y Patrones Semanales
* **Matriz de Frecuencia Cruzada:** Análisis de co-ocurrencia de dos problemas simultáneos en una misma reseña (ej. *Pésimo Trato + Espera Excesiva*).
* **Heatmap de Reseñas:** Categoría de queja en Google vs. Día de la semana de mayor impacto cualitativo.

[Página 6 - Co-Ocurrencia de Problemas y Patrones Semanales](assets/nlp_reclamos/06pagina6.png)

</details>

---

### Arquitectura del Pipeline y Scripts (Proyecto 1)

Los scripts correspondientes a este módulo están organizados en `scripts/nlp_reclamos/`:

1.  **`01_procesar_expedientes.py`:** Motor de NLP en Python que realiza extracción de entidades nombradas (NER), fuzzy matching contra nóminas de personal y cálculo automatizado del puntaje ISR.
2.  **`02_pipeline_duckdb.py`:** Pipeline SQL de alto rendimiento en DuckDB que agrupa eventos en episodios clínicos y realiza la vinculación espacio-temporal (-60 a +30 días) entre reclamos, encuestas CASS y transacciones de MasterKey.
3.  **`03_anonimizador_reclamos.py`:** Módulo de seguridad y data masking que aplica el reemplazo de nombres de funcionarios y salting criptográfico de RUTs mediante la librería `Faker`.