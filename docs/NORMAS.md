# NORMAS.md — Reglas generales de mis proyectos

> Aplica a los 3 proyectos actuales. Una sola fuente de verdad: las reglas viven en las secciones 1–5; el historial vive en la sección 7.
> Última compactación: _(nunca)_ · Entradas en el registro: 3

---

## 0. Cómo usar y mantener este archivo

**Para Claude / el agente:** lee este archivo al inicio de cada sesión y aplícalo a cualquier cambio de código o interfaz. Si una norma choca con una petición explícita del usuario, gana la petición y se registra la excepción. Para revisar un proyecto, sigue la auditoría por interfaz de la sección 6.

**Actualizar (registro tipo commit):**
- Cada cambio a una norma se anota en la sección 7 con el formato `AAAA-MM-DD · tipo(área): resumen`.
- Tipos: `add` (norma nueva), `change` (norma modificada), `remove` (norma eliminada), `fix` (corrección de redacción/error), `note` (decisión o excepción por proyecto).
- Áreas: `seg`, `uso`, `ux-ui`, `perf` (rendimiento), `proc` (proceso/este archivo).
- Una línea por entrada. Si necesita explicación, máx. 2 líneas de sangría.
- La norma se edita **en su sección** y además se deja el registro. Nunca solo una de las dos.

**Soft autoclean / compact (cuando aplique alguno de estos disparadores):**
1. El registro supera **30 entradas**, o
2. El archivo supera **~400 líneas**, o
3. Pasaron **90 días** desde la última compactación.

Procedimiento (no destructivo):
1. Copia el estado actual a `NORMAS.archive.md` bajo un encabezado con la fecha (`## Archivo AAAA-MM-DD`).
2. Fusiona entradas del registro por área y trimestre en una sola línea resumen (`2026-Q4 · seg: 5 cambios — ver archivo`). Conserva intactas las 10 entradas más recientes.
3. Elimina de las secciones 1–5 las normas duplicadas, contradictorias o ya reemplazadas (el registro dice cuál reemplazó a cuál).
4. Si una norma no se ha aplicado ni citado en 6 meses, muévela a `NORMAS.archive.md`, no la borres.
5. Actualiza arriba "Última compactación" y el contador de entradas, y registra `note(proc): compactación`.

Regla de oro: compactar nunca debe cambiar el significado de las normas vigentes, solo reducir ruido.

---

## 1. Seguridad

**S1 — Secretos fuera del código.** Claves, tokens y contraseñas van en variables de entorno o gestor de secretos; jamás en el repositorio ni en el cliente. Incluir `.env` en `.gitignore` y mantener un `.env.example` sin valores reales.
**S2 — Mínimo privilegio.** Cada usuario, token, rol y servicio tiene solo los permisos que necesita. Revisar permisos al añadir una función.
**S3 — Validar toda entrada.** Validar y sanear en el servidor, aunque el cliente ya valide. Consultas parametrizadas (nunca concatenar SQL). Escapar salida para evitar XSS.
**S4 — Autenticación sólida.** Contraseñas con hash moderno (argon2/bcrypt), nunca en texto plano. Ofrecer 2FA donde sea posible. Sesiones con expiración y cierre de sesión real.
**S5 — Autorización en el servidor.** Comprobar en backend que el usuario puede acceder a cada recurso (evitar IDOR). Ocultar un botón en la UI no es seguridad.
**S6 — HTTPS siempre.** Cookies con `Secure`, `HttpOnly`, `SameSite`. Cabeceras de seguridad básicas (CSP, X-Content-Type-Options, HSTS).
**S7 — Datos personales con cuidado.** Recoger solo lo necesario, no registrar datos sensibles en logs, cifrar lo sensible en reposo y en tránsito.
**S8 — Errores que no filtran.** Mensajes al usuario genéricos; el detalle técnico va a logs internos. Nunca mostrar trazas ni rutas.
**S9 — Dependencias al día.** Revisar vulnerabilidades (`npm audit`, `pip-audit`, Dependabot) antes de cada entrega; fijar versiones; evitar paquetes sin mantenimiento.
**S10 — Copias y recuperación.** Respaldos periódicos probados. Una copia que nunca se restauró no cuenta.
**S11 — Limitar abuso.** Rate limiting en login, registro, recuperación de contraseña y APIs públicas.
**S12 — Subidas de archivos.** Validar tipo real y tamaño, renombrar, almacenar fuera de la raíz pública.

---

## 2. Usabilidad

**U1 — Una pantalla, una acción principal.** El usuario debe saber en 3 segundos qué hacer. Un solo botón primario por vista.
**U2 — Pocos pasos.** Reducir toques/clics al mínimo para las tareas frecuentes (pedir, buscar, pagar, repetir). Recordar elecciones previas y ofrecer "repetir".
**U3 — Retroalimentación inmediata.** Toda acción responde en <100 ms con algo visible (estado de carga, confirmación, cambio de color). Nunca dejar al usuario sin saber si funcionó.
**U4 — Prevenir y perdonar errores.** Confirmar solo acciones destructivas; ofrecer "Deshacer" en lugar de "¿Seguro?" cuando se pueda. Mensajes de error que digan qué pasó y cómo arreglarlo, en lenguaje humano.
**U5 — Formularios cortos.** Pedir lo mínimo, autocompletar, teclado correcto según el campo (email, número, teléfono), validar en línea y no al final.
**U6 — Lenguaje claro.** Palabras de uso cotidiano, sin jerga técnica. Botones con verbos concretos ("Agregar al carrito", no "Aceptar").
**U7 — Estados completos.** Diseñar siempre: cargando (skeleton), vacío (con guía de qué hacer), error (con reintento) y éxito.
**U8 — Accesibilidad básica.** Contraste mínimo 4.5:1 en texto, objetivos táctiles ≥ 44×44 px, navegación por teclado, `alt` en imágenes, no depender solo del color para comunicar.
**U9 — Rendimiento percibido.** Meta: carga inicial < 2 s en móvil medio. El detalle técnico vive en la sección 4 (R1–R31).
**U10 — Funciona sin conexión estable.** Manejar red lenta o caída con mensajes claros y reintentos; no perder lo que el usuario escribió.
**U11 — Probar con gente real.** Antes de dar por buena una interfaz, que 2–3 personas ajenas a la tecnología hagan la tarea principal sin ayuda. Si dudan, se rediseña.

---

## 3. Interfaces UX/UI (principios de diseño)

Referencia de estilo: fluidez y claridad tipo Rappi/Apple (preferencia: Apple), con apoyo en Google Material para patrones probados.

**D1 — Jerarquía visual clara.** Tamaño, peso y color guían la mirada: título > contenido > metadatos. Mucho espacio en blanco; no saturar.
**D2 — Sistema de diseño propio.** Definir tokens una sola vez (color, tipografía, espaciado, radios, sombras) y reutilizarlos. Nada de valores sueltos.
**D3 — Espaciado en cuadrícula de 4/8 px.** Todo margen y padding son múltiplos de 4 u 8.
**D4 — Tipografía.** Una familia principal (sistema: SF/Inter/Roboto), máx. 2 pesos habituales, escala consistente (p. ej. 12/14/16/20/24/32). Cuerpo mínimo 16 px en web móvil.
**D5 — Color con intención.** Un color de marca vibrante para la acción principal, neutros para la estructura, y colores semánticos fijos (éxito/aviso/error). Máx. 1 acento fuerte por pantalla. Soportar modo claro y oscuro.
**D6 — Bordes redondeados y tarjetas.** Radios generosos y consistentes (12–20 px en tarjetas), sombras suaves y sutiles, imágenes protagonistas.
**D7 — Movimiento con propósito.** Transiciones de 150–300 ms con curvas suaves (ease-out); animar para explicar un cambio (entrada, salida, foco), nunca por decorar. Respetar `prefers-reduced-motion`.
**D8 — Mobile first.** Diseñar primero para el pulgar: acciones principales abajo, navegación inferior, zonas táctiles amplias. Luego escalar a tablet/escritorio.
**D9 — Consistencia ante todo.** El mismo componente se ve y se comporta igual en todo el producto. Reutilizar antes de crear.
**D10 — Gestos y patrones familiares.** Usar convenciones de iOS/Android (deslizar para volver, pull-to-refresh, hojas inferiores) en vez de inventar interacciones.
**D11 — Contenido real, no relleno.** Diseñar con textos e imágenes reales y casos extremos (nombres largos, sin imagen, 0 resultados).
**D12 — Iconografía coherente.** Un solo set de iconos, mismo grosor y tamaño; siempre acompañar iconos ambiguos con texto.

---

## 4. Rendimiento

**Principio:** que la app nunca se sienta lenta. Medir primero (R29–R31), optimizar lo que duele y no añadir infraestructura (Redis, colas, balanceador) hasta que el dato lo justifique.
**Etiquetas:** `[F]` frontend · `[A]` red/API · `[B]` backend/datos · `[I]` infraestructura/observabilidad.
**Prioridad:** ★★★ casi siempre aplica · ★★ si aparece el síntoma · ★ cuando escala.
**Metas (Core Web Vitals):** LCP < 2.5 s · INP < 200 ms · CLS < 0.1.

### Frontend
**R1 — [F]★★★ Skeletons de carga** en toda vista que espere datos (ver U7); nada de pantalla en blanco ni spinner sin contexto.
**R2 — [F]★★★ Imágenes en WebP o AVIF**, con tamaños responsivos (`srcset`) y dimensiones fijas para evitar saltos (CLS).
**R3 — [F]★★★ Lazy loading** en imágenes fuera de pantalla (`loading="lazy"`). La imagen principal (LCP) no se carga lazy.
**R4 — [F]★★ Dividir el JavaScript por página/ruta** (code splitting); cargar bajo demanda lo pesado (gráficos, editores, mapas).
**R5 — [F]★★ Renderizar en servidor o en el build** (SSR/SSG) las páginas de contenido; reservar el render en cliente para lo interactivo.
**R6 — [F]★★ Scripts de terceros después de la página** (`defer`/`async`, o tras la primera interacción): analítica, chat, píxeles.
**R7 — [F]★★ Virtualizar listas largas** (más de ~100 elementos) para pintar solo lo visible.
**R8 — [F]★★ Evitar re-renders innecesarios:** estado lo más local posible, claves estables, memoización solo donde se midió el problema.
**R9 — [F]★★ UI optimista:** actualizar la interfaz sin esperar al servidor en acciones de bajo riesgo (me gusta, marcar, agregar) y revertir con aviso si falla.
**R10 — [F]★★ Fuentes alojadas por nosotros,** máx. 2 familias y pocos grosores, `font-display: swap` (ver D4).
**R11 — [F]★★ Debounce (~300 ms) en búsqueda y autocompletado,** cancelando la petición anterior.
**R12 — [F]★ Quitar dependencias sin usar** (`depcheck`, `knip`) y revisar el tamaño del bundle antes de cada entrega.

### Red y API
**R13 — [A]★★★ Evitar llamadas duplicadas a la API:** caché y deduplicación en cliente (React Query/SWR o equivalente).
**R14 — [A]★★★ Paginar resultados** (preferible por cursor) con un límite por defecto; nunca devolver colecciones enteras.
**R15 — [A]★★ Lanzar en paralelo las peticiones independientes** (`Promise.all`); en serie solo lo que depende de lo anterior.
**R16 — [A]★★★ Timeout en toda API externa,** con reintento limitado y espera creciente; una API ajena lenta no debe colgar la nuestra.
**R17 — [A]★★ Transmitir (streaming) las respuestas de IA token a token** en vez de esperar a la respuesta completa.
**R18 — [A]★★★ Compresión Brotli o gzip** activa en respuestas de texto (HTML, JS, CSS, JSON).
**R19 — [A]★★★ Cachear estáticos en el navegador:** archivos con hash en el nombre y `Cache-Control` largo e inmutable.
**R20 — [A]★★ Servir los archivos estáticos desde una CDN.**

### Backend y datos
**R21 — [B]★★★ Arreglar consultas N+1:** carga anticipada (joins/`include`) o consultas por lote.
**R22 — [B]★★★ Índices para las consultas más comunes;** comprobar con `EXPLAIN` y no indexar a ciegas.
**R23 — [B]★★ Pedir solo las columnas necesarias:** nada de `SELECT *` en rutas calientes.
**R24 — [B]★★ Usar un pool de conexiones** a la base de datos con tamaño acorde al servidor.
**R25 — [B]★★ Mandar las tareas lentas a una cola de trabajos** (correos, informes, procesado de archivos, trabajos largos de IA) y responder de inmediato con estado.
**R26 — [B]★ Cachear con Redis los datos más consultados,** con TTL e invalidación definidos. Solo si se mide que la base es el cuello de botella.

### Infraestructura y observabilidad
**R27 — [I]★ Acercar el servidor a la base de datos** (misma región/zona); la latencia entre ambos se paga en cada consulta.
**R28 — [I]★ Repartir el tráfico entre varias instancias** (balanceador) cuando una sola no aguante la carga.
**R29 — [I]★★ Rastrear peticiones lentas con OpenTelemetry** (trazas) para saber dónde se va el tiempo.
**R30 — [I]★★ Monitorear la velocidad de usuarios reales** (RUM, Core Web Vitals), no solo pruebas de laboratorio.
**R31 — [F]★★★ Probar en celulares reales y con redes lentas** (limitar a 3G/4G en las herramientas del navegador) antes de dar algo por terminado.

---

## 5. Proceso y calidad (transversal)

**P1 — Checklist antes de entregar:** secretos fuera (S1), entradas validadas (S3), estados vacío/carga/error (U7), contraste y tamaño táctil (U8), imágenes optimizadas y JS dividido (R2, R4), prueba en móvil real con red lenta (R31).
**P2 — Commits pequeños y descriptivos.** Formato `tipo(área): resumen` (`feat`, `fix`, `refactor`, `docs`, `chore`).
**P3 — Cambios de diseño se prueban en el dispositivo real**, no solo en el navegador de escritorio.
**P4 — Documentar decisiones** que se aparten de estas normas con `note(...)` en el registro.

---

## 6. Excepciones, auditoría y notas por proyecto

_Usar `note` en el registro y resumir aquí solo lo vigente._

| Proyecto | Norma | Excepción / ajuste | Fecha |
|---|---|---|---|
| Proyecto 1 | — | — | — |
| Proyecto 2 | — | — | — |
| Proyecto 3 | — | — | — |

### Auditoría por proyecto e interfaz

Procedimiento para revisar si las normas (S, U, D, R) se cumplen y aplicar las necesarias:
1. Listar las interfaces (pantallas/vistas) del proyecto.
2. Para cada interfaz marcar cada norma relevante: ✅ cumple · 🔧 hay que aplicarla · ➖ no aplica (con razón de una línea).
3. Aplicar los 🔧 por prioridad: primero ★★★ y seguridad; ★ solo si hay síntoma o escala.
4. Registrar el resultado en la sección 7 con `note(perf)` o el área que corresponda, p. ej. `note(perf): Proyecto 1/Inicio — aplicadas R1,R2,R13; N/A R26 (sin Redis aún)`.
5. Actualizar la tabla de estado.

| Proyecto | Interfaz | ✅ | 🔧 | ➖ | Última revisión |
|---|---|---|---|---|---|
| Proyecto 1 (jemPOS) | Auditado 2026-11-06: 12 interfaces (caja, ventas, turno, gastos, fiados, inventario, proveedores, precios mayoristas, dashboard, perfil, comisiones, auth/públicas) | R1–R31 revisadas por interfaz | Aplicadas: fuentes locales, contraste, táctil 44 px, reduced-motion, límites, 409, LIMIT | Pendiente: índices SQL, Tailwind compilado, idempotencia |
| Proyecto 2 | — | — | — | — | — |
| Proyecto 3 | — | — | — | — | — |

---

## 7. Registro de cambios (más reciente arriba)

```
2026-11-05 · change(uso): U9 ahora remite a la sección 4 para el detalle técnico de rendimiento
2026-11-05 · add(perf): sección Rendimiento R1–R31 (frontend, red/API, backend, infraestructura), metas Core Web Vitals y procedimiento de auditoría por interfaz; renumeradas secciones 5–7
2026-11-05 · add(proc): creación del archivo con normas base de seguridad (S1–S12), usabilidad (U1–U11), UX/UI (D1–D12) y proceso (P1–P4)
```
- 2026-11-06 note(perf): Proyecto 1/jemPOS — auditoría por interfaz; aplicadas R5,R9,R12,R25,R29 y contraste/táctil; pendientes índices (R17) y Tailwind compilado (R22)
