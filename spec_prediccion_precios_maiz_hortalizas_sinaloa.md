# Spec — Predicción de precio de venta de maíz y hortalizas en Sinaloa

**Versión:** 1.1
**Región objetivo:** Estado de Sinaloa (núcleo productivo: Culiacán, Navolato, Guasave, Ahome, Angostura; sur del estado para chiles)
**Cultivos en alcance:** Maíz blanco (grano) y hortalizas de exportación (tomate, chile, pepino, berenjena, calabacita, ejote)
**Fuera de alcance:** Cualquier otro cultivo (trigo, frijol, garbanzo, cártamo, papa, mango, caña, sorgo, etc.)

---

## 1. Resumen ejecutivo

El objetivo es construir un sistema que **prediga el precio de venta (en pesos mexicanos) del maíz blanco y de las principales hortalizas de Sinaloa**, con la predicción anclada a **las fechas en que arrancan las cosechas de cada cultivo**, que es cuando la decisión de comercialización del productor tiene mayor valor económico.

El problema tiene una particularidad estructural que condiciona todo el diseño: **los dos productos no se forman igual**.

- El **maíz blanco** es un mercado **semi-administrado**: existe un *precio piso / esquema de comercialización* negociado cada ciclo entre gobierno federal, gobierno estatal e industria, más apoyos (precio de garantía a pequeños, incentivos IAR/PUT, apoyo de traslado), y por encima de ese piso el precio queda "libre al alza" ligado al **mercado internacional de granos (futuros de Chicago) y al tipo de cambio**. Predecir maíz es, en buena medida, predecir *piso + prima de mercado*.
- Las **hortalizas** son un mercado de **exportación pura**: ~97–99% del volumen va a Estados Unidos, los precios se forman en USD y se convierten a MXN. Aquí dominan el **tipo de cambio**, la **política comercial de EE. UU. (aranceles / acuerdo de suspensión / precios mínimos al tomate)**, la oferta competidora (Florida) y la logística de cruce por Nogales.

Por eso el sistema se diseña como **dos familias de modelos separadas** (maíz vs. hortalizas), con un núcleo común de ingeniería de datos.

---

## 2. Objetivo, preguntas de negocio y alcance

### 2.1 Objetivo

Entregar, en la fecha de arranque de cosecha de cada cultivo, una **predicción puntual y un intervalo de confianza** del precio de venta esperado en pesos mexicanos, para uno o varios horizontes dentro de la ventana de cosecha.

### 2.2 Preguntas de negocio que debe responder

1. ¿A qué precio (MXN) se venderá la tonelada de maíz blanco al inicio y durante la ventana de cosecha del ciclo Otoño–Invierno?
2. ¿A qué precio (MXN/kg o MXN/caja) se venderá cada hortaliza principal al arranque y durante su temporada de corte/exportación?
3. ¿Cuál es el **rango probable** (no solo el valor central), para que el productor decida cuándo contratar, retener o cubrir con instrumentos de riesgo?
4. **¿Cuándo conviene sembrar para que la cosecha caiga en la ventana con mayor precio/margen esperado?** (Recomendador de ventana de siembra — ver §9.bis.)

### 2.3 Dentro de alcance

- Maíz blanco grano, ciclo Otoño–Invierno (el dominante en Sinaloa).
- Hortalizas: tomate (saladette/bola), chile (jalapeño, serrano, morrón/poblano), pepino (americano/persa/pickle), berenjena, calabacita, ejote.
- Predicción de precio **al productor / a primera venta** y, como variante opcional, precio mayorista de referencia.
- **Recomendador de ventana de siembra**: sugerir la(s) fecha(s) de siembra que hacen que la cosecha caiga en el periodo de mayor precio/margen esperado (§9.bis).

### 2.4 Fuera de alcance (explícito)

- Todo cultivo distinto de maíz y hortalizas.
- Predicción de **rendimiento** o **superficie** como entregable final (se usan solo como variables de entrada).
- Optimización de la decisión de venta / cobertura (cuándo contratar o cubrir con instrumentos de riesgo) — fuera de alcance; el recomendador de §9.bis solo cubre la decisión de **siembra**, no la de comercialización.
- Maíz amarillo de importación como producto a vender (sí entra como variable de referencia internacional).

---

## 3. Definición precisa de la variable objetivo (target)

Definir bien el target es el 80% del éxito. Se proponen **objetivos separados por producto**, con unidades en MXN.

### 3.1 Maíz blanco

- **Target primario:** precio de venta al productor de maíz blanco grano, **MXN por tonelada**, a pie de parcela / centro de acopio en Sinaloa.
- **Construcción recomendada:** serie compuesta que combine (a) el *precio piso del esquema de comercialización* del ciclo, (b) los apoyos vigentes (garantía a pequeños, incentivo IAR/PUT, traslado) y (c) el componente de mercado spot mayorista. Como referencia spot pública se usa **SNIIM** (precios al mayoreo) ajustada a base productor.
- **Granularidad temporal:** semanal durante la ventana de cosecha; resolución mensual para histórico largo.

> Nota metodológica: como una parte del precio del maíz es **administrada**, conviene modelar el target en dos componentes: `precio = piso_política + prima_mercado`. El piso se conoce o se anticipa por reglas/negociación; la prima es lo que el modelo estadístico realmente debe predecir (ligada a futuros + FX + basis de importación).

### 3.2 Hortalizas

- **Target primario por cultivo:** precio de primera venta / FOB, **MXN por kilogramo** (alternativa: MXN por caja/empaque estándar del producto).
- **Construcción recomendada:** precio de referencia del mercado de destino (USD) **× tipo de cambio**, validado contra precios mayoristas nacionales (SNIIM / centrales de abasto) para el volumen que se queda en mercado interno.
- **Modelos por cultivo y por tipo comercial** (p. ej. tomate saladette vs. bola se comportan distinto). Empezar con tomate y chile (mayor valor y superficie), luego pepino/berenjena.
- **Granularidad temporal:** semanal (la exportación es muy sensible a ventanas cortas).

---

## 4. Fechas de cosecha y ventanas de predicción (entregable clave solicitado)

Sinaloa tiene su grueso productivo en el ciclo **Otoño–Invierno (OI)**: siembra en otoño, cosecha entre el invierno tardío y la primavera. A continuación, las fechas operativas propuestas para anclar las predicciones. Son fechas-guía; el sistema debe ajustarlas cada ciclo con el avance real de siembra/cosecha de SIAP.

### 4.1 Maíz blanco (ciclo Otoño–Invierno)

| Hito | Periodo típico |
|---|---|
| Siembra | Octubre – diciembre (óptimo agronómico ~15 nov en Valle de Culiacán) |
| **Inicio de cosecha (ancla de predicción)** | **~1 de abril** (primeros cortes de grano) |
| Pico de cosecha / comercialización | Abril – junio |
| Cierre de ventana | Junio – julio |

- **Fecha ancla de la predicción:** **1 de abril** de cada año (arranque de cosecha OI).
- **Emisión anticipada:** generar una primera predicción en **1 de febrero (T-60 días)** y re-emitir mensual/semanal hasta el cierre, porque el *esquema de comercialización* suele negociarse entre marzo y mayo y mueve fuertemente el target.
- **Horizontes a predecir:** precio al arranque (abril), pico (mayo) y cierre (junio).

### 4.2 Hortalizas (ciclo Otoño–Invierno de exportación)

| Hito | Periodo típico |
|---|---|
| Siembra | Septiembre – noviembre |
| **Inicio de cosecha / primeros cortes (ancla de predicción)** | **~1 de noviembre** |
| Pico de exportación | Enero – marzo |
| Cierre de ventana | Mayo – junio |

- **Fecha ancla de la predicción:** **1 de noviembre** (primeros cortes en Culiacán/Navolato).
- **Emisión anticipada:** primera predicción en **1 de octubre (T-30 días)**, re-emisión semanal durante toda la temporada (nov–may).
- **Horizontes a predecir:** arranque (nov–dic), pico (ene–mar) y cierre (abr–may).
- Para chiles del **sur del estado**, el desarrollo se intensifica septiembre–noviembre; tratar el sur como subregión con su propia ancla.

> Regla operativa común: el sistema **se dispara automáticamente en la fecha ancla** de cada cultivo y vuelve a correr en cadencia (mensual para maíz, semanal para hortalizas) hasta cerrar la ventana de cosecha.

---

## 5. Variables de entrada: cuáles valen la pena y cuáles no

Esta es la sección donde se define el conjunto de *features*. Cada variable se clasifica por **prioridad**:
- **Crítica** — alto poder predictivo esperado y dato disponible; incluir desde el MVP.
- **Condicional** — útil pero costosa de obtener, ruidosa o de cobertura parcial; incluir en fase 2.
- **Descartar** — bajo aporte, redundante o no accionable; documentar por qué no se usa.

La relevancia difiere por producto, así que se marca **M** (maíz) y **H** (hortalizas).

### 5.1 Climatológicas

| Variable | Justificación | Prioridad | Fuente sugerida |
|---|---|---|---|
| **Nivel de almacenamiento de presas** (Lázaro Cárdenas, etc.) | El OI de Sinaloa es ~94% bajo riego; el agua disponible determina superficie sembrada y, vía oferta, el precio. | **Crítica (M, H)** | CONAGUA / Sistema Nacional de Información del Agua (SINA) |
| **Temperatura (máx/mín diaria)** | Afecta rendimiento: bajo la base 10 °C se frena el maíz; máximas 30–35 °C en llenado de grano lo reducen. Heladas dañan hortalizas. | **Crítica (M, H)** | SMN-CONAGUA, NASA POWER |
| **Heladas en zonas competidoras (Florida)** | La ventana exportadora de hortalizas existe porque Florida se hiela en invierno; una helada allá sube el precio del producto sinaloense. | **Crítica (H)** | NOAA / NWS |
| **Precipitación acumulada** | Humedad de suelo, complemento al riego, riesgo de exceso en cosecha. | Condicional (M, H) | SMN-CONAGUA, NASA POWER |
| **Índice de sequía (Monitor de Sequía de México)** | Resume estrés hídrico regional; buen predictor de oferta del ciclo. | Condicional (M, H) | CONAGUA – Monitor de Sequía |
| **ENSO (El Niño / La Niña)** | Modula lluvias del noroeste y heladas en EE. UU. con meses de anticipación → señal *temprana*. | Condicional (M, H) | NOAA CPC (ONI) |
| **NDVI / evapotranspiración satelital** | Proxy directo del estado del cultivo y de la oferta esperada. | Condicional (M, H) | MODIS / Sentinel-2 |
| Radiación solar, horas-luz | Aporte marginal sobre temperatura; alta colinealidad. | Descartar (fase 1) | — |

### 5.2 Mercantiles / mercado

| Variable | Justificación | Prioridad | Fuente sugerida |
|---|---|---|---|
| **Precio histórico del propio cultivo** | Autorregresivo: la mejor base predictiva de un precio es su propia historia + estacionalidad. | **Crítica (M, H)** | SNIIM, SIAP |
| **Futuros de maíz (CBOT/CME)** | El componente "libre al alza" del maíz blanco se ancla al mercado internacional y a la paridad de importación. | **Crítica (M)** | CME Group |
| **Tipo de cambio USD/MXN** | ~97–99% de hortalizas se vende en USD; el precio en MXN depende casi linealmente del FX. También afecta paridad de importación del maíz. | **Crítica (M, H)** | Banxico |
| **Precio de garantía / esquema de comercialización / precio piso** | Define el suelo del maíz cada ciclo (p. ej. piso negociado por tonelada + apoyos). | **Crítica (M)** | SEGALMEX / SADER / Gob. Sinaloa |
| **Superficie sembrada / intención de siembra** | *Leading indicator* de oferta: menos hectáreas → menos volumen → precio más alto. Disponible antes de cosechar. | **Crítica (M, H)** | SIAP, Consejo Estatal de Desarrollo Rural, CESAVESIN |
| **Avance/volumen de cosecha semanal** | Marca el ritmo de llegada de oferta al mercado dentro de la ventana. | Crítica (M, H) | SIAP |
| **Paridad de importación / basis del Golfo** | El maíz nacional compite con importado ("precio de indiferencia"); fija techo/piso efectivo. | Condicional (M) | USDA, referencias de flete |
| **Inventarios y balance USDA (WASDE)** | Oferta-demanda global de maíz, mueve futuros. | Condicional (M) | USDA WASDE |
| **Precios mayoristas en centrales de abasto destino** (CDMX, Guadalajara, Monterrey, Tijuana) | Para la fracción de hortaliza que se queda en mercado interno. | Condicional (H) | SNIIM |
| Demanda industrial (harinera, almidonera, pecuaria) | Difícil de obtener en alta frecuencia. | Condicional (M) | Cámaras industriales |

### 5.3 Geopolíticas / política comercial

| Variable | Justificación | Prioridad | Fuente sugerida |
|---|---|---|---|
| **Aranceles / antidumping / acuerdo de suspensión y precios mínimos al tomate de EE. UU.** | Cambia directamente la competitividad y el precio efectivo de exportación; ya redujo superficie sembrada de tomate. | **Crítica (H)** | USDOC, comunicados sectoriales (CAADES/AMHPAC) |
| **Costo de insumos: diésel, fertilizantes (urea), gas natural** | Entra al cálculo del costo de producción que fija el piso negociado del maíz y la rentabilidad de hortaliza. | Condicional (M, H) | SNIIM insumos, índices internacionales |
| **Controversias T-MEC / política de maíz transgénico** | Riesgo de choque comercial de mediano plazo; útil como bandera de evento, no como variable continua. | Condicional (M) | Diario Oficial, prensa especializada |
| Decisiones de política monetaria/inflación general | Su efecto ya está capturado vía FX y precios de insumos; baja señal incremental. | Descartar (fase 1) | — |

### 5.4 Sociales / logísticas

| Variable | Justificación | Prioridad | Fuente sugerida |
|---|---|---|---|
| **Bloqueos carreteros / paros de productores / cierres de Nogales** | Interrumpen el flujo de exportación y mueven el precio en el corto plazo; modelar como **variable de evento** (dummy con duración). | Condicional (H, M) | Prensa regional, boletines de asociaciones |
| **Disponibilidad y costo de jornaleros** (migración de Guerrero/Oaxaca/Veracruz) | Las hortalizas son intensivas en mano de obra; faltante de cuadrillas afecta corte y oferta. | Condicional (H) | Encuestas sectoriales, registros estatales |
| **Contexto de inseguridad en Sinaloa** | Episodios recientes afectaron logística, cosecha y siembra; modelar como evento/régimen, con cautela. | Condicional (M, H) | Fuentes oficiales / prensa |
| Indicadores demográficos / consumo nacional | Cambian lento; poca señal en ventana de cosecha. | Descartar (fase 1) | — |

### 5.5 Variables de calendario (siempre incluir)

Mes, semana del año, **semana relativa dentro de la ventana de cosecha**, ciclo agrícola (OI/PV), y banderas de hitos (inicio de cosecha, pico, cierre). Son baratas y capturan la fuerte estacionalidad de ambos mercados.

---

## 6. Fuentes de datos (resumen)

| Dominio | Fuentes principales |
|---|---|
| Precios agrícolas nacionales | **SNIIM** (mayoreo), **SIAP** (producción, superficie, rendimiento, avance) |
| Precios/política del maíz | **SEGALMEX**, **SADER**, Gobierno de Sinaloa (esquema de comercialización) |
| Mercados internacionales | **CME/CBOT** (futuros maíz), **USDA** (WASDE, exportaciones) |
| Tipo de cambio | **Banxico** |
| Clima / agua | **CONAGUA/SMN**, **SINA** (presas), **Monitor de Sequía**, **NASA POWER**, **NOAA CPC** (ENSO), **MODIS/Sentinel** (NDVI) |
| Política comercial EE. UU. | **USDOC**, asociaciones (CAADES, AMHPAC) |
| Eventos sociales/logísticos | Boletines de asociaciones de productores, prensa regional |

> Acción requerida de tu lado: confirmar **acceso histórico** a SNIIM y SIAP con suficiente profundidad (idealmente 10+ años) y la **latencia de publicación** de cada fuente, porque define qué variables están realmente disponibles en la fecha de predicción (evitar *data leakage*).

---

## 7. Enfoque de modelado

### 7.1 Estructura general

Dos familias de modelos independientes:
- **Modelo Maíz** (un target, ciclo OI).
- **Modelos Hortalizas** (uno por cultivo/tipo comercial; arrancar con tomate y chile).

### 7.2 Línea base → modelos avanzados

1. **Baselines** (obligatorios para tener piso de comparación): naïve estacional, media móvil, **SARIMAX** con regresores exógenos, Prophet.
2. **Modelos de machine learning** sobre features rezagadas: **gradient boosting (LightGBM/XGBoost)**; opcionalmente un modelo global tipo árbol que comparta señal entre hortalizas.
3. **Intervalos de predicción:** regresión por cuantiles (P10/P50/P90) o *conformal prediction*. **El intervalo es tan importante como el punto**, porque la decisión del productor es de gestión de riesgo.
4. **Maíz — descomposición:** modelar `prima_mercado` con ML (futuros + FX + basis) y sumar el `piso_política` conocido/anticipado por reglas.

### 7.3 Validación

- **Validación temporal** (rolling/expanding origin), nunca aleatoria: respetar el orden del tiempo.
- Evaluar **por ciclo de cosecha** (un ciclo entero como test), porque el negocio se juega ciclo a ciclo.
- Cuidar *leakage*: una variable solo puede usarse si estaba publicada en la fecha de predicción (respetar latencias).

### 7.4 Métricas

- Puntual: **MAE**, **RMSE**, **MAPE/sMAPE**.
- Intervalos: **pinball loss** y cobertura empírica (¿el 80% real cae dentro del intervalo 80%?).
- Negocio: **acierto direccional** (¿predijo sube/baja respecto al ciclo previo?) y error en la fecha ancla vs. precio realizado.

---

## 8. Arquitectura / pipeline (alto nivel)

```
Ingesta (APIs/scraping: SNIIM, SIAP, Banxico, CME, CONAGUA, NOAA, USDA)
        │
        ▼
Capa de datos cruda  ──►  Limpieza y armonización (frecuencia, unidades MXN, calendario agrícola)
        │
        ▼
Ingeniería de features (rezagos, estacionalidad, eventos, NDVI, presas, FX, futuros)
        │
        ▼
Entrenamiento por producto (baseline + GBM + cuantiles) con validación temporal
        │
        ▼
Disparador por fecha ancla (1-nov hortalizas / 1-abr maíz) + re-emisión en cadencia
        │
        ▼
Salida: predicción puntual (MXN) + intervalo P10/P50/P90 + drivers principales
```

---

## 9. Salidas / entregables del sistema

Para cada cultivo y cada fecha ancla:

- **Precio esperado** (MXN/ton para maíz; MXN/kg o MXN/caja para hortalizas).
- **Intervalo P10–P90**.
- **Horizontes:** arranque, pico y cierre de la ventana de cosecha.
- **Explicabilidad:** los 3–5 *drivers* que más empujan la predicción (p. ej. "FX +X%, presas −Y%, futuros +Z%"), para que la cifra sea accionable y auditable.

---

## 9.bis Recomendador de ventana de siembra

Esta es la salida que convierte el sistema de un *predictor* en una *herramienta de decisión*. La idea: trabajar **hacia atrás** desde el calendario de precios hasta la fecha de siembra.

### 9.bis.1 La cadena lógica

```
fecha de siembra d
      │  (modelo de duración del cultivo: días a cosecha / grados-día)
      ▼
fecha de cosecha h(d)
      │  (curva de precio predicho a lo largo de la ventana)
      ▼
precio esperado P(h)  ──┐
                         ├──►  margen esperado = P(h)·Y(d) − Costos(d)
rendimiento esperado Y(d)┘     (Y depende de la fecha de siembra)
      │
      ▼
elegir d* que maximiza el margen ajustado por riesgo, dentro de la ventana agronómica factible
```

### 9.bis.2 Por qué NO es simplemente "cosechar cuando el precio esté más alto"

Aquí está la parte fina. Apuntar ciegamente al pico de precio puede destruir valor por tres razones:

1. **El rendimiento depende de la fecha de siembra.** El óptimo agronómico del maíz en el Valle de Culiacán ronda mediados de noviembre; sembrar fuera de esa ventana baja el rendimiento (temperaturas mínimas bajo la base de 10 °C frenan el desarrollo; máximas de 30–35 °C en llenado de grano lo reducen). Mover la siembra para perseguir un mejor precio puede costar toneladas. El objetivo correcto es **margen** (precio × rendimiento − costos), **no precio**.

2. **Hay una ventana agronómica factible que no se puede violar.** El ciclo Otoño–Invierno, la disponibilidad de agua de presas, el riesgo de heladas y el permiso/ordenamiento de siembra (CESAVESIN) acotan las fechas posibles. El recomendador optimiza **dentro** de ese rango, no sobre el calendario completo.

3. **El consejo es endógeno si todos lo siguen.** Si muchos productores siembran para cortar en la misma "semana cara", la sobreoferta aplana ese pico. Un productor individual es tomador de precio, pero a nivel agregado el pico se mueve. El sistema debe **advertir** esta limitación y, idealmente, condicionar la predicción de precio a la oferta esperada (superficie/intención de siembra del ciclo).

4. **La incertidumbre en siembra es mucho mayor que en cosecha.** En la fecha de siembra estamos prediciendo un precio a 4–6 meses; el intervalo es ancho. La recomendación se entrega como **ventana** (no un día único) y **ajustada por riesgo** (p. ej. maximizar un cuantil bajo del margen, o media–varianza / CVaR), nunca como una cifra puntual con falsa precisión.

### 9.bis.3 Dónde aporta más valor

- **Hortalizas: alto valor.** La estacionalidad de precio es fuerte —los primeros cortes (nov–dic) suelen alcanzar precios elevados por baja oferta y porque las zonas competidoras de EE. UU. (Florida) se hielan en invierno. El clásico "sembrar temprano para cortar temprano" es justo lo que este módulo cuantifica, midiendo el premio del precio temprano contra el mayor riesgo agronómico de adelantar la siembra.
- **Maíz: valor moderado.** Como el precio tiene un **piso administrado** (esquema de comercialización + apoyos), la estacionalidad intra-ciclo es más plana y el margen de maniobra de la fecha de siembra es estrecho (la ventana OI es corta). Aquí el recomendador sirve más para **evitar** fechas de siembra que pegan en bajas de precio o castigos de rendimiento, que para "cazar" un pico.

### 9.bis.4 Componentes nuevos requeridos

| Componente | Qué hace | Fuente / método |
|---|---|---|
| **Modelo de duración del cultivo (fenología)** | Convierte fecha de siembra → fecha de cosecha por cultivo/variedad. Idealmente por **grados-día (GDD)** usando temperatura, no días fijos (el frío del OI alarga el ciclo). | Catálogos de variedades, ensayos agronómicos, INIFAP; temperatura SMN/NASA POWER |
| **Curva rendimiento vs. fecha de siembra** | Penaliza siembras fuera del óptimo agronómico. | Ensayos históricos, SIAP por fecha de siembra, literatura local |
| **Curva de precio sobre toda la ventana** | El predictor de precio debe entregar `P(semana)` para **todas** las semanas de cosecha candidatas, no solo la fecha ancla. | Extensión del modelo de §7 (predicción multi-horizonte) |
| **Costos de producción por fecha** | Riego adicional, riesgo de helada/plaga, mano de obra según calendario. | Agrocostos FIRA/SADER, CAADES |
| **Restricciones de la ventana factible** | Acota fechas por agua disponible, ciclo OI, heladas y permiso de siembra. | SINA (presas), CONAGUA, CESAVESIN |

### 9.bis.5 Salida concreta del recomendador

Para cada cultivo, en el momento de planeación previo al ciclo:

- **Ventana de siembra recomendada** (rango de fechas, no un día).
- **Fecha de cosecha resultante** y **precio/margen esperado** con su intervalo P10–P90.
- **Comparativo** contra la práctica habitual del productor ("sembrar en tu fecha usual ≈ $X; adelantar 2 semanas ≈ $Y con +riesgo de helada").
- **Advertencias de riesgo**: incertidumbre del horizonte largo y la nota de endogeneidad (si muchos adelantan, el premio se diluye).



## 10. Supuestos y riesgos

| Riesgo | Impacto | Mitigación |
|---|---|---|
| El precio del maíz es **semi-administrado**, no puro mercado | El modelo de mercado solo, sesgaría | Descomponer en piso de política + prima de mercado |
| **Choques exógenos de política comercial EE. UU.** (aranceles tomate) | Rompen la serie histórica | Variables de evento/régimen + reentrenamiento ante quiebre estructural |
| **Latencia / huecos** en fuentes públicas | Leakage o falta de input en fecha ancla | Auditar latencias; usar solo datos disponibles a la fecha; imputación documentada |
| **Pocos ciclos** de histórico limpio | Sobreajuste | Priorizar features con sentido agronómico/económico; regularización; validación por ciclo |
| **Eventos sociales** (bloqueos, inseguridad) difíciles de anticipar | Errores puntuales grandes | Modelar como eventos; comunicar siempre con intervalo, no solo punto |
| Heterogeneidad de **tipos comerciales** de hortaliza | Un modelo único pierde precisión | Un modelo por cultivo/tipo |
| **Recomendar siembra a horizonte largo** (4–6 meses) | Intervalo de precio muy ancho; falsa precisión | Entregar **ventana** ajustada por riesgo, no fecha única; comunicar incertidumbre |
| **Endogeneidad** del consejo de siembra (si muchos lo siguen, el pico se aplana) | Sesga el premio esperado | Condicionar el precio a oferta/superficie esperada; advertir la limitación |
| **Perseguir precio sacrificando rendimiento** | Margen real menor al esperado | Optimizar **margen** (precio×rendimiento−costos), no precio; curva rendimiento vs. siembra |

---

## 11. Roadmap por fases

**Fase 0 — Datos y definiciones (descubrimiento).** Confirmar acceso histórico a SNIIM/SIAP, fijar la construcción exacta del target por producto, mapear latencias de fuentes. *Salida: catálogo de datos + definición de target validada.*

**Fase 1 — MVP Maíz.** Baselines + GBM con features críticas (futuros, FX, piso/esquema, presas, superficie, histórico). Predicción anclada al 1-abr con re-emisión desde 1-feb. *Salida: predicción de maíz con intervalo y backtest por ciclo.*

**Fase 2 — MVP Hortalizas (tomate y chile).** Modelos por cultivo con FX, política comercial EE. UU., heladas Florida, presas, superficie. Predicción anclada al 1-nov con re-emisión semanal. *Salida: predicción de tomate y chile con intervalo.*

**Fase 3 — Ampliación y robustez.** Sumar pepino/berenjena/calabacita/ejote, NDVI, ENSO, eventos sociales; **predicción de precio multi-horizonte (curva sobre toda la ventana de cosecha)**, calibración de intervalos (conformal); explicabilidad. *Salida: sistema de precio completo en cadencia automática.*

**Fase 4 — Recomendador de ventana de siembra (§9.bis).** Sobre la curva de precio de la fase 3, sumar modelo de fenología (GDD), curva rendimiento vs. fecha de siembra, costos por fecha y restricciones de ventana factible; optimizar margen ajustado por riesgo. Priorizar hortalizas (mayor valor) y luego maíz. *Salida: ventana de siembra recomendada con margen esperado e intervalo.*

**Fase 5 (opcional, fuera de este spec).** Recomendación de comercialización/cobertura (cuándo contratar o cubrir) sobre las predicciones.

---

## 12. Preguntas abiertas para cerrar contigo

1. ¿El precio objetivo debe ser **al productor / primera venta** o **mayorista de referencia**? (Cambia la fuente del target.)
2. Para hortalizas, ¿prefieres el target en **MXN/kg** o en **MXN/caja** (empaque estándar del producto)?
3. ¿Qué profundidad de **histórico** tienes disponible en SNIIM/SIAP (años)?
4. ¿El sistema es para **un productor/empresa específica** (con sus propios precios de contrato) o un **referente regional** público?
5. ¿Incluimos desde fase 1 la **subregión sur** (chiles) con su propia ancla, o se pospone?
