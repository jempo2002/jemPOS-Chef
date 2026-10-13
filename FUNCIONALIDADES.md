# Funcionalidades de jemPOS Chef

POS y contabilidad en la nube para restaurantes pequeños y medianos, versión de jemPOS adaptada a la operación de un restaurante (mesas, comandas, cocina, domicilios, recetas).

Este documento lista todo lo que hace la aplicación hoy. **Se actualiza cada vez que se integra una funcionalidad, utilidad o herramienta nueva.**

- **Última actualización:** 13 de octubre de 2026
- **Estado de cada función:**
  - ✅ **En main**: aprobada y en la rama principal.
  - 🧪 **Solo en test**: hecha y probada en `test`, esperando aprobación para pasar a `main`.
  - 📋 **Pendiente**: definida o anunciada, todavía sin desarrollar.

---

## Resumen rápido

| Área | Función | Estado | Plan |
|---|---|---|---|
| Página pública | Landing en `/` con planes y botón Ingresar | ✅ | — |
| Acceso | Login, recuperar contraseña, elegir sede, roles | ✅ | Todos |
| Salón | Mesas, zonas y plano del salón | ✅ | Todos |
| Salón | Toma de pedidos y comandas a cocina | ✅ | Todos |
| Salón | Pedidos para llevar | ✅ | Todos |
| Salón | Domicilios y recaudo de domiciliarios | ✅ | Todos |
| Salón | Adicionales (salsas, extras) | 🧪 | Todos |
| Cocina | Pantalla de cocina y bar | ✅ | Completo, Cadena |
| Caja | Turnos de caja, gastos, arqueo y Nequi | ✅ | Todos |
| Caja | Cuenta dividida | ✅ | Completo, Cadena |
| Caja | Pago Mixto | ✅ | Todos |
| Inventario | Carta, stock por sede y kardex | ✅ | Todos |
| Inventario | Recetas, insumos y costo por plato | ✅ | Completo, Cadena |
| Administración | Reportes y contabilidad | 🧪 | Todos |
| Administración | Calificación del servicio con QR | 🧪 | Todos |
| Administración | Sedes (multisede) | ✅ | Completo, Cadena |
| Administración | Equipo (usuarios por rol) | ✅ | Todos |
| Plataforma | Panel Master (alta de restaurantes, planes, pagos) | ✅ | — |
| Interfaz | Menú móvil con barra inferior y hoja "Más" | 🧪 | Todos |
| Plataforma | Funcionamiento sin conexión (cola en el dispositivo) | ✅ | Todos |
| Plataforma | Restaurante demo | ✅ (contraseña fija 🧪) | — |
| Plataforma | Despliegue en Railway | ✅ | — |
| Facturación | Factura electrónica DIAN | 📋 | Cadena |

---

## 1. Página pública (landing) ✅

- Página de inicio pública en la raíz `/` de la app, con el mismo estilo del landing de jemPOS (tarjetas, sombras, animaciones de entrada, botones con brillo) en paleta naranja.
- Secciones con los planes y precios, y botón **Ingresar** que lleva a `/login`.
- El login tiene un botón **Volver a Inicio** (esquina superior izquierda).

## 2. Acceso, roles y seguridad ✅

- **Login** con correo y contraseña, y cierre de sesión.
- **Recuperar contraseña** por correo (`/olvide-password` y enlace con token).
- **Elegir sede** al entrar cuando el Admin tiene varias sedes. Cajero, Mesero y Cocina quedan atados a su sede.
- **Roles:** Master (dueño de la plataforma), Admin, Cajero, Mesero y Cocina. Cada pantalla y cada acción revisa el rol.
- **Sesión revalidada en cada petición** (si el usuario se desactiva o cambia de sede, la sesión se corta).
- **Modo solo lectura** cuando vence la suscripción del restaurante.
- **Seguridad:** protección CSRF, cabeceras de seguridad (CSP), páginas privadas sin caché, límite de intentos de login, registro de eventos de seguridad.
- **Redis** solo para sesiones y contadores de intentos (obligatorio en producción).

## 3. Salón

### 3.1 Mesas y zonas ✅
- Zonas (terraza, salón, segundo piso…) y mesas por sede, configurables en **Mesas › Configurar**.
- **Plano del salón** con el estado de cada mesa: libre, ocupada o por cobrar. El estado sale del pedido abierto, así nunca queda una mesa "ocupada" por error.
- Pantalla de mesas clara, con botón **Nuevo para llevar** a mano.
- Zonas y mesas con borrado suave; el nombre de una mesa eliminada se puede volver a usar.

### 3.2 Pedidos y comandas ✅
- Tomar el pedido desde la carta, con líneas "por agregar" antes de enviarlas.
- **Enviar a cocina:** crea una comanda por estación (cocina o bar); lo que no necesita preparación (una gaseosa) queda entregado de una vez.
- **El inventario se descuenta al enviar la comanda**, no al cobrar (los platos con receta descuentan sus ingredientes).
- **Imprimir comanda.**
- **Precuenta** con propina sugerida; el pedido pasa a "por cobrar".
- **Mover el pedido** a otra mesa.
- **Anular** una línea o el pedido. Anular algo ya enviado a cocina exige Admin y motivo, y se decide si vuelve al inventario o es merma.
- Cobro en una sola venta con la propina aparte (la propina no es venta).

### 3.3 Para llevar ✅
- Botón **Nuevo para llevar** en Mesas. Funciona igual que una mesa: carta, enviar a cocina, precuenta, cobro y cuenta dividida.
- Nombre y teléfono del cliente, y número consecutivo por sede (#1, #2…).
- Se puede cobrar antes de que la cocina termine. Sale de la lista al marcar **Entregado al cliente** (o sola, 12 horas después de cobrado).

### 3.4 Domicilios y recaudo ✅
- Menú **Domicilios**: se toman como un para llevar, con dirección.
- **Despachar:** asigna el domiciliario, cómo paga el cliente (efectivo, Nequi, tarjeta o Mixto) y con cuánto paga; imprime la hoja del domiciliario con lo que debe cobrar y el vuelto.
- Estados: por despachar, despachado, entregado y liquidado.
- **Caja › Domiciliarios:** una tarjeta por domiciliario con el efectivo exacto que debe entregar (solo lo cobrado en efectivo, o la parte en efectivo de un Mixto), en rojo si pasa el tope (por defecto $150.000, lo ajusta el Admin).
- **"Recibí $X"** crea las ventas en la caja abierta y suma el efectivo al cajón; si la cifra cambió mientras tanto, no registra nada y avisa.
- Cambiar el método en el camino, reasignar domiciliario y "No lo entregó": solo Cajero y Admin, y quedan en auditoría.
- Domiciliarios con borrado suave; no son usuarios ni gastan cupo del plan.

### 3.5 Adicionales (salsas y extras) 🧪
- Un producto se marca como **adicional**: no sale en la grilla de la carta, se agrega a un plato con el botón **+ Adicional**.
- Precio propio como adicional (0 = gratis). Puede tener receta o inventario como cualquier producto.
- Va amarrado a su plato: descuenta al enviar a cocina, sale en la comanda del plato, se cobra y entra a la venta con su costo. Anular el plato anula sus adicionales.
- Un adicional gratis no sale en la alerta de costos.

## 4. Cocina ✅ (Completo y Cadena)

- Pantalla **Cocina** con las comandas de la sede, separadas por estación (cocina y bar).
- Avance de cada comanda: nueva, preparando, lista y entregada.
- Se actualiza sola cada pocos segundos y solo trae lo que cambió (nuevas, avanzadas, ítems anulados, entregadas).

## 5. Caja

### 5.1 Turnos de caja ✅
- **Abrir caja** con base, **registrar gastos** durante el turno y **cerrar con arqueo**.
- Una caja abierta por sede.
- La propina en efectivo suma a lo que debe haber en el cajón, pero no es venta.
- La caja muestra **cuánto debe haber en Nequi/Daviplata** (pagos por Nequi y la parte por transferencia de los Mixto).
- Panel de **Domiciliarios** dentro de Caja (ver 3.4).

### 5.2 Cuenta dividida ✅ (Completo y Cadena, solo Cajero y Admin)
- **Por productos:** cada persona toma unidades de las líneas (2 cervezas se parten 1 y 1) y paga una venta propia, con su método y su propina.
- **En partes iguales:** el total se reparte en N montos y queda una sola venta con los platos reales.
- La propina de cada parte arranca en 0.
- Imprimir las cuentas. Todo o nada en una sola transacción; reenviarlo desde la cola sin conexión no cobra dos veces.

### 5.3 Métodos de pago y pago Mixto ✅
- Efectivo, Nequi/Daviplata, tarjeta y **Mixto** (parte efectivo y parte otro método).
- Mixto disponible en el cobro normal, en cada parte de la cuenta dividida, en los domicilios y en los gastos de caja.

## 6. Carta e inventario

### 6.1 Carta ✅
- Una sola carta por restaurante, con categorías y productos (platos y bebidas), precio y estación (cocina, bar o ninguna).
- Productos con borrado suave: uno eliminado sigue en las ventas viejas y su receta queda guardada.

### 6.2 Inventario por sede ✅
- Stock **por sede** para lo que se vende hecho (gaseosas, cervezas) y para los insumos.
- Entradas, salidas y ajustes manuales.
- **Kardex** (`movimientos_inventario`) que nunca se borra ni se edita (lo impiden triggers en la base): toda corrección es un movimiento nuevo con el stock de antes y de después.
- Aviso cuando falta stock al tomar el pedido.

### 6.3 Recetas, insumos y costo por plato ✅ (Completo y Cadena)
- Pantalla **Recetas** (solo Admin).
- Insumos en gramos, mililitros o unidades, con stock por sede.
- Compras en kg, lb o L con **costo promedio ponderado**.
- Receta por plato y **costo por plato** calculado (el costo del producto se sincroniza con la receta).
- **Alerta de costos** cuando los ingredientes pasan del 35 % del precio (porcentaje ajustable por restaurante).
- Descuento de ingredientes al enviar a cocina, validando el consumo sumado por insumo.
- Historial de movimientos por insumo y exportación de costos en CSV.

## 7. Administración

### 7.1 Reportes y contabilidad 🧪 (solo Admin)
- Pantalla **Reportes** con pestañas **Resumen, Ventas, Gastos, Cierres de caja** y **Calificaciones**.
- **Estado de resultados:** ventas, costo de lo vendido, utilidad bruta, gastos y utilidad neta, con comparación contra el periodo anterior.
- Filtros por periodo y por sede (o todas las sedes).
- Reglas: la propina no es venta (se muestra aparte), las mermas restan, las compras de insumos van al flujo de caja (no al estado de resultados), el costo de cada plato queda fijo al cobrar.
- **Gastos por categoría**, registrados en Caja o por el Admin fuera de la caja (arriendo, nómina). Un gasto no se borra, se anula con motivo, y no se puede fechar hacia atrás.
- **Exportar a CSV** (ventas por producto, gastos, etc.).

### 7.2 Calificación del servicio 🧪
- Al cobrar sale un **QR** (imprimible) y, si el pedido tiene teléfono, la opción de enviarlo por **WhatsApp**.
- El cliente califica **comida y atención** de 1 a 5 con comentario opcional, sin cuenta, en una página pública. Una vez por pedido, hasta 3 días después del cobro.
- En **Reportes › Calificaciones:** promedios, reparto de notas, resultados por mesero y por sede, porcentaje de pedidos calificados y comentarios.
- El Admin puede ocultar una calificación con motivo y apagar el QR al cobrar.

### 7.3 Sedes (multisede) ✅
- Pantalla **Sedes** (Admin): crear, editar y eliminar (borrado lógico).
- Básico: una sede. Completo y Cadena: hasta 4 sedes. Cada sede extra paga el 50 % del plan al mes ($34.500 en Completo, $49.500 en Cadena) y un montaje único de $79.000.
- Triggers en la base impiden pasar el tope o bajar a Básico con varias sedes.

### 7.4 Equipo ✅
- Pantalla **Equipo** (Admin): crear, editar y desactivar usuarios con su rol y su sede.
- Tope de usuarios por plan (4 por sede en Básico, 8 por sede en Completo y Cadena), con aviso para cambiar de plan.

### 7.5 Inicio ✅
- Pantalla de inicio con el plan del restaurante, la sede actual y qué funciones incluye el plan.

## 8. Planes ✅

| Plan | Precio/mes | Incluye |
|---|---|---|
| **Básico** | $49.000 | Mesas, para llevar, domicilios, propina, caja y turnos. Una sede. |
| **Completo** | $69.000 | Todo lo anterior + pantalla de cocina, cuenta dividida, recetas e insumos, hasta 4 sedes. |
| **Cadena** | $99.000 | Todo lo de Completo + factura electrónica DIAN (📋 pendiente). |

- Implementación según número de platos: $119.000 (hasta 40), $219.000 (41 a 100), $379.000 (más de 100). Incluye el primer mes gratis.
- Más de 4 sedes = plan corporativo (📋 no se desarrolla aún).
- Cuando una función no viene en el plan, la app lo dice y ofrece pasarse al siguiente por WhatsApp.

## 9. Panel Master ✅

- Pantalla **Restaurantes** solo para el Master (dueño de la plataforma).
- Crear un restaurante (tienda + sede principal + Admin) con un mes de prueba del plan elegido. No hay registro público.
- Cambiar el plan, registrar pagos (renovar la suscripción), marcar el montaje de una sede como cobrado y eliminar un restaurante.

## 10. Interfaz

### 10.1 Menú móvil 🧪
- En celular y tablet: **barra fija abajo** con hasta 4 accesos según el rol, más un botón **Más** que abre una hoja con el resto (Operación, Administración, Cuenta).
- En computador: barra arriba en una fila y menú de cuenta en el avatar.

### 10.2 Funcionamiento sin conexión ✅
- Cola en el dispositivo: si se cae el internet, se pueden seguir abriendo mesas y para llevar, agregar platos y cobrar; todo se envía al volver la conexión.
- Cada línea y cada cobro lleva un identificador del dispositivo, así reenviarlos no duplica nada.

## 11. Herramientas, demo y despliegue

- **Restaurante demo** ✅: `scripts/crear_demo.py` crea "Demo Chef" con 10 mesas, carta, insumos, 4 recetas y un usuario por rol (`admin@`, `cajero@`, `mesero@`, `cocina@demo.chef`). 🧪 La contraseña de las cuentas demo queda fija y `migrar_railway.ps1 -Demo` la crea o repone en Railway.
- **Migrar la base de Railway** ✅: `scripts/migrar_railway.ps1` aplica el esquema y todas las migraciones en orden; con `-Nombre` y `-Correo` crea el usuario Master.
- **Crear Master** ✅: `scripts/crear_master.py`.
- **Despliegue** ✅: Railway (app web + MySQL + Redis) en https://jempos-chef-production.up.railway.app, hoy desde la rama `test`. 📋 Pendiente: producción desde `main` y dominio con Cloudflare.
- **Pruebas automáticas** ✅: pytest contra MariaDB y Redis reales, con pruebas por módulo (salón, recetas, cuenta dividida, domicilios, pago Mixto, reportes, adicionales, calificaciones, menú, sedes, planes, Master, login).

## 12. Reglas generales del producto

- **Borrado suave** en todo lo que se pueda (mesas, zonas, productos, insumos, sedes, usuarios, domiciliarios, gastos, calificaciones) sin afectar rendimiento ni seguridad.
- **Registros que nunca se borran:** el kardex de inventario y la auditoría; se corrigen con movimientos nuevos.
- El inventario se descuenta **al enviar a cocina**, no al cobrar.
- Todo filtrado por restaurante y por sede.

## 13. Pendiente o por definir 📋

- Factura electrónica DIAN (plan Cadena).
- Plan corporativo para más de 4 sedes.
- Salsa como sub-receta dentro de la receta de un plato, y limitar qué adicionales aplican a cada plato.
- Producción desde `main` y dominio con Cloudflare.

---

## Historial de cambios de este documento

| Fecha | Cambio |
|---|---|
| 2026-10-13 | Primera versión: todo lo que hay en `main` y `test` a esta fecha. |
