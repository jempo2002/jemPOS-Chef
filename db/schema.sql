-- ============================================================
-- jemPOS Chef: esquema base de la base de datos, SIN datos
-- Origen: reconstruido del codigo de jemPOS (jempo2002/jemPOS), main al 2026-10-06 (hasta 2026-10-06_comisiones_promotoras
--         + scripts/migracion_tiendas_tipo.sql + scripts/migracion_uso_diario.sql)
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Para que sirve: punto de partida de la base de jemPOS Chef. Es el esquema
-- de jemPOS (que no se modifica, solo se toma como fuente) con todas sus
-- migraciones ya aplicadas; los cambios propios de restaurante van encima.
--
--   mysql -u USUARIO -p BASE < db/schema.sql
--
-- Las rutas migrations/, scripts/ y app/ que se citan abajo son del repo
-- jemPOS.
--
-- Como se armo: el volcado original nunca estuvo en el repo, asi que este
-- archivo se reconstruyo leyendo el codigo. Hay dos niveles de certeza:
--
--  * Exacto: lo que crea o modifica una migracion del repo (tablas, columnas,
--    tipos, enums, claves y FK con nombre). Cada bloque cita su migracion.
--  * Inferido: el resto de las 15 tablas del volcado de julio. Los nombres de
--    columna salen de las consultas de app/ y son los que usa el codigo; los
--    tipos, longitudes, nombres de clave y acciones ON DELETE son la lectura
--    mas probable (las validaciones de app/services fijan casi todas las
--    longitudes). Va marcado con "inferido".
--
-- Cuadra con lo que DEPLOY.md dice del volcado: 15 tablas base + 2 de
-- 2026-09-22 = 17 tablas y 37 claves foraneas.
--
-- Lo que falta: el volcado trae 7 triggers y el codigo solo deja ver uno
-- (bi_gastos_caja_fecha_creacion, ver al final). Para tener el esquema exacto,
-- reemplazar este archivo por el de produccion (estructura y triggers, sin
-- filas):
--
--   mysqldump --no-data --triggers --routines --skip-comments \
--     -h HOST -P PUERTO -u USUARIO -p railway > schema.sql
--
-- Sin `IF NOT EXISTS` en columnas (MySQL no lo admite), pero si en las tablas:
-- correrlo sobre una base que ya tiene el esquema no cambia nada.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

-- ------------------------------------------------------------
-- tiendas: cada negocio cliente (multi-tenant por id_tienda)
-- ------------------------------------------------------------
-- nit/estado: 2026-09-23_*; alegra_*: 2026-09-30_*; plan_id/trial_ends_at:
-- 2026-10-01; id_promotora: 2026-10-06; tipo: scripts/migracion_tiendas_tipo.
-- Inferido: nombre_negocio, telefono, es_restaurante, estado_suscripcion,
-- fechas de suscripcion y fecha_creacion. estado_suscripcion la app solo la
-- escribe como 'activa' o 'suspendida'.
CREATE TABLE IF NOT EXISTS `tiendas` (
  `id_tienda` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `nombre_negocio` varchar(150) NOT NULL,
  `nit` varchar(50) DEFAULT NULL,
  `telefono` varchar(20) DEFAULT NULL,
  `es_restaurante` tinyint(1) NOT NULL DEFAULT 0,
  `estado` enum('Activo','Suspendido','Eliminado') NOT NULL DEFAULT 'Activo',
  `estado_suscripcion` varchar(20) NOT NULL DEFAULT 'activa',
  `fecha_inicio_suscripcion` date DEFAULT NULL,
  `fecha_fin_suscripcion` date DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  `alegra_api_user` varchar(120) DEFAULT NULL,
  `alegra_api_token` varchar(500) DEFAULT NULL,
  `alegra_number_template_id` varchar(20) DEFAULT NULL,
  `alegra_impuesto_id` varchar(20) DEFAULT NULL,
  `plan_id` varchar(20) DEFAULT NULL,
  `trial_ends_at` date DEFAULT NULL,
  `id_promotora` bigint(20) UNSIGNED DEFAULT NULL,
  `tipo` enum('cliente','promotora','interna') NOT NULL DEFAULT 'cliente',
  PRIMARY KEY (`id_tienda`),
  UNIQUE KEY `uq_tiendas_nit` (`nit`),
  KEY `fk_tiendas_promotora` (`id_promotora`),
  CONSTRAINT `fk_tiendas_promotora` FOREIGN KEY (`id_promotora`) REFERENCES `usuarios` (`id_usuario`) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- usuarios: Master (sin tienda), Admin y Cajero
-- ------------------------------------------------------------
-- correo/cc: 2026-09-23_liberar_unicos_eliminados; pago_jornada/horas_jornada:
-- 2026-09-27; es_promotora: 2026-10-06. Inferido: el resto. id_tienda admite
-- NULL: el Master no tiene tienda y al Admin se le asigna despues de crearlo.
CREATE TABLE IF NOT EXISTS `usuarios` (
  `id_usuario` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED DEFAULT NULL,
  `nombre_completo` varchar(150) NOT NULL,
  `correo` varchar(191) NOT NULL,
  `clave_hash` varchar(255) NOT NULL,
  `rol` enum('Master','Admin','Cajero') NOT NULL DEFAULT 'Cajero',
  `cc` varchar(50) DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  `pago_jornada` decimal(12,2) DEFAULT NULL,
  `horas_jornada` decimal(4,1) DEFAULT NULL,
  `es_promotora` tinyint(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id_usuario`),
  UNIQUE KEY `uq_usuarios_correo` (`correo`),
  UNIQUE KEY `uq_usuarios_cc` (`cc`),
  KEY `idx_usuarios_tienda` (`id_tienda`),
  CONSTRAINT `fk_usuarios_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- categorias (inferido)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `categorias` (
  `id_categoria` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(120) NOT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (`id_categoria`),
  KEY `idx_categorias_tienda_nombre` (`id_tienda`, `nombre`),
  CONSTRAINT `fk_categorias_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- proveedores
-- ------------------------------------------------------------
-- telefono_2: 2026-09-16; dia_visita: 2026-10-04; nit: 2026-10-05;
-- estado_activo: scripts/db_soft_delete_migration. Inferido: el resto.
-- `celular` es el telefono 1.
CREATE TABLE IF NOT EXISTS `proveedores` (
  `id_proveedor` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre_empresa` varchar(150) NOT NULL,
  `nombre_contacto` varchar(150) DEFAULT NULL,
  `dia_visita` varchar(12) DEFAULT NULL,
  `celular` varchar(20) DEFAULT NULL,
  `telefono_2` varchar(20) DEFAULT NULL,
  `nit` varchar(15) DEFAULT NULL,
  `correo` varchar(100) DEFAULT NULL,
  `detalles` text DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_proveedor`),
  KEY `idx_proveedores_tienda_activo` (`id_tienda`, `estado_activo`),
  CONSTRAINT `fk_proveedores_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- productos
-- ------------------------------------------------------------
-- tipo, unidad_medida, precio_mayorista, empaque_*, stock decimal:
-- 2026-09-25_mayorista_servicios_unidades; alegra_item_id/iva_porcentaje:
-- 2026-09-30; estado_activo y sus indices: scripts/db_soft_delete_migration
-- y scripts/db_indexes; fk_productos_proveedores: 2026-09-16 (documentada).
-- Inferido: el resto. uq_productos_tienda_codigo_barras: inventory_service
-- traduce el IntegrityError que menciona codigo_barras a "codigo duplicado",
-- y al desactivar un producto pone su codigo en NULL para liberarlo.
-- es_preparado: plato con receta (recetas_productos); sales_service aun lo lee.
CREATE TABLE IF NOT EXISTS `productos` (
  `id_producto` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_categoria` bigint(20) UNSIGNED DEFAULT NULL,
  `nombre` varchar(150) NOT NULL,
  `tipo` enum('Producto','Servicio') NOT NULL DEFAULT 'Producto',
  `unidad_medida` varchar(20) NOT NULL DEFAULT 'Unidad',
  `codigo_barras` varchar(64) DEFAULT NULL,
  `precio_costo` decimal(12,2) NOT NULL DEFAULT 0.00,
  `precio_venta` decimal(12,2) NOT NULL DEFAULT 0.00,
  `precio_mayorista` decimal(12,2) DEFAULT NULL,
  `empaque_nombre` varchar(30) DEFAULT NULL,
  `empaque_cantidad` decimal(12,3) DEFAULT NULL,
  `precio_empaque` decimal(12,2) DEFAULT NULL,
  `stock_actual` decimal(12,3) DEFAULT NULL,
  `stock_minimo_alerta` decimal(12,3) DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `es_preparado` tinyint(1) NOT NULL DEFAULT 0,
  `id_proveedor` bigint(20) UNSIGNED DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  `alegra_item_id` varchar(30) DEFAULT NULL,
  `iva_porcentaje` decimal(5,2) NOT NULL DEFAULT 0.00,
  PRIMARY KEY (`id_producto`),
  UNIQUE KEY `uq_productos_tienda_codigo_barras` (`id_tienda`, `codigo_barras`),
  KEY `idx_productos_codigo_barras` (`codigo_barras`),
  KEY `idx_productos_estado_activo` (`estado_activo`),
  KEY `idx_productos_id_categoria` (`id_categoria`),
  KEY `idx_productos_tienda_activo` (`id_tienda`, `estado_activo`),
  KEY `idx_productos_proveedor` (`id_proveedor`),
  CONSTRAINT `fk_productos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_productos_categorias` FOREIGN KEY (`id_categoria`) REFERENCES `categorias` (`id_categoria`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_productos_proveedores` FOREIGN KEY (`id_proveedor`) REFERENCES `proveedores` (`id_proveedor`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- insumos y recetas_productos: inventario de cocina (restaurante)
-- ------------------------------------------------------------
-- Columnas tomadas del codigo de restaurante que ya se retiro de la app
-- (historial de git) y de la venta de platos con receta que sigue en
-- sales_service. scripts/db_mvp_cleanup.sql las borraria; el volcado las trae.
-- estado_activo: scripts/db_soft_delete_migration. Inferido: tipos y FK.
CREATE TABLE IF NOT EXISTS `insumos` (
  `id_insumo` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(150) NOT NULL,
  `stock_actual` decimal(12,3) NOT NULL DEFAULT 0.000,
  `unidad_medida` varchar(20) NOT NULL DEFAULT 'Unidad',
  `costo_unitario` decimal(12,2) NOT NULL DEFAULT 0.00,
  `id_proveedor` bigint(20) UNSIGNED DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_insumo`),
  KEY `idx_insumos_tienda_activo` (`id_tienda`, `estado_activo`),
  KEY `idx_insumos_proveedor` (`id_proveedor`),
  CONSTRAINT `fk_insumos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_insumos_proveedores` FOREIGN KEY (`id_proveedor`) REFERENCES `proveedores` (`id_proveedor`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `recetas_productos` (
  `id_receta` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `id_insumo` bigint(20) UNSIGNED NOT NULL,
  `cantidad_requerida` decimal(12,3) NOT NULL,
  PRIMARY KEY (`id_receta`),
  UNIQUE KEY `uq_recetas_producto_insumo` (`id_producto`, `id_insumo`),
  KEY `idx_recetas_insumo` (`id_insumo`),
  CONSTRAINT `fk_recetas_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_recetas_insumos` FOREIGN KEY (`id_insumo`) REFERENCES `insumos` (`id_insumo`) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- listas_precios: 2026-09-22_cartera_b2b (exacta). Ya no se usa para cobrar
-- (2026-09-25 la reemplazo por productos.precio_mayorista) pero se conserva.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `listas_precios` (
  `id_lista` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(100) NOT NULL,
  `descuento_pct` decimal(5,2) NOT NULL DEFAULT 0.00,
  `min_pedidos_recurrentes` int(10) UNSIGNED NOT NULL DEFAULT 0,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_lista`),
  UNIQUE KEY `uq_listas_tienda_nombre` (`id_tienda`, `nombre`),
  KEY `idx_listas_tienda_activo` (`id_tienda`, `estado_activo`),
  CONSTRAINT `fk_listas_precios_tiendas`
    FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `chk_listas_descuento_pct` CHECK (`descuento_pct` >= 0 AND `descuento_pct` <= 100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- clientes: B2C (fiados de caja) y B2B (mayorista)
-- ------------------------------------------------------------
-- cedula: 2026-09-16; tipo/nit/id_lista_precios: 2026-09-22; estado_activo:
-- scripts/db_soft_delete_migration. Inferido: el resto.
-- uq_clientes_tienda_telefono: cartera_service responde "Ese telefono ya
-- pertenece a otro cliente" ante el IntegrityError.
CREATE TABLE IF NOT EXISTS `clientes` (
  `id_cliente` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(150) NOT NULL,
  `cedula` varchar(20) DEFAULT NULL,
  `telefono` varchar(20) DEFAULT NULL,
  `tipo` enum('B2C','B2B') NOT NULL DEFAULT 'B2C',
  `nit` varchar(30) DEFAULT NULL,
  `id_lista_precios` bigint(20) UNSIGNED DEFAULT NULL,
  `direccion` varchar(255) DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_cliente`),
  UNIQUE KEY `uq_clientes_tienda_cedula` (`id_tienda`, `cedula`),
  UNIQUE KEY `uq_clientes_tienda_telefono` (`id_tienda`, `telefono`),
  KEY `idx_clientes_tienda_activo` (`id_tienda`, `estado_activo`),
  KEY `idx_clientes_tienda_tipo` (`id_tienda`, `tipo`, `estado_activo`),
  KEY `idx_clientes_lista` (`id_lista_precios`),
  CONSTRAINT `fk_clientes_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_clientes_listas_precios`
    FOREIGN KEY (`id_lista_precios`) REFERENCES `listas_precios` (`id_lista`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- turnos_caja: apertura, arqueo y cierre de caja
-- ------------------------------------------------------------
-- Indices: scripts/db_indexes. Inferido: el resto. Los usuarios van con
-- RESTRICT: un usuario con turnos no se borra, se desactiva (core.py).
CREATE TABLE IF NOT EXISTS `turnos_caja` (
  `id_turno` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_usuario_apertura` bigint(20) UNSIGNED NOT NULL,
  `id_usuario_cierre` bigint(20) UNSIGNED DEFAULT NULL,
  `fecha_apertura` timestamp NOT NULL DEFAULT current_timestamp(),
  `fecha_cierre` timestamp NULL DEFAULT NULL,
  `monto_inicial` decimal(12,2) NOT NULL DEFAULT 0.00,
  `monto_final_esperado` decimal(12,2) DEFAULT NULL,
  `monto_final_real` decimal(12,2) DEFAULT NULL,
  `estado_turno` enum('Abierto','Cerrado') NOT NULL DEFAULT 'Abierto',
  `observaciones` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id_turno`),
  KEY `idx_turnos_tienda_estado` (`id_tienda`, `estado_turno`),
  KEY `idx_turnos_caja_estado_turno` (`estado_turno`),
  KEY `idx_turnos_caja_fecha_apertura` (`fecha_apertura`),
  KEY `idx_turnos_usuario_apertura` (`id_usuario_apertura`),
  KEY `idx_turnos_usuario_cierre` (`id_usuario_cierre`),
  CONSTRAINT `fk_turnos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_turnos_usuario_apertura` FOREIGN KEY (`id_usuario_apertura`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE,
  CONSTRAINT `fk_turnos_usuario_cierre` FOREIGN KEY (`id_usuario_cierre`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- ventas: una fila por venta; un fiado es una venta 'Fiada/Pendiente'
-- ------------------------------------------------------------
-- metodo_pago/monto_efectivo/monto_transferencia: 2026-09-27; alegra_invoice_id,
-- cufe, url_pdf, estado_dian: 2026-09-30; indices: scripts/db_indexes.
-- Inferido: el resto. turno y cajero RESTRICT (2026-09-23_tiendas_estado_
-- eliminado lo describe asi). tipo_descuento: la app hoy siempre guarda
-- 'NINGUNO'; el codigo viejo tambien usaba 'PORCENTAJE'.
CREATE TABLE IF NOT EXISTS `ventas` (
  `id_venta` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_turno` bigint(20) UNSIGNED DEFAULT NULL,
  `id_cajero` bigint(20) UNSIGNED NOT NULL,
  `id_cliente` bigint(20) UNSIGNED DEFAULT NULL,
  `numero_venta` varchar(20) DEFAULT NULL,
  `subtotal` decimal(12,2) NOT NULL DEFAULT 0.00,
  `tipo_descuento` varchar(20) NOT NULL DEFAULT 'NINGUNO',
  `valor_descuento` decimal(12,2) NOT NULL DEFAULT 0.00,
  `descuento_aplicado` decimal(12,2) NOT NULL DEFAULT 0.00,
  `total_final` decimal(12,2) NOT NULL,
  `metodo_pago` enum('Efectivo','Nequi/Daviplata','Tarjeta','Mixto') NOT NULL,
  `monto_efectivo` decimal(12,2) DEFAULT NULL,
  `monto_transferencia` decimal(12,2) DEFAULT NULL,
  `estado_venta` enum('Pagada','Fiada/Pendiente','Anulada') NOT NULL DEFAULT 'Pagada',
  `observaciones` varchar(255) DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  `alegra_invoice_id` varchar(30) DEFAULT NULL,
  `cufe` varchar(120) DEFAULT NULL,
  `url_pdf` varchar(2000) DEFAULT NULL,
  `estado_dian` varchar(40) DEFAULT NULL,
  PRIMARY KEY (`id_venta`),
  KEY `idx_ventas_estado_venta` (`estado_venta`),
  KEY `idx_ventas_fecha_creacion` (`fecha_creacion`),
  KEY `idx_ventas_id_tienda` (`id_tienda`),
  KEY `idx_ventas_turno` (`id_turno`),
  KEY `idx_ventas_cajero` (`id_cajero`),
  KEY `idx_ventas_cliente` (`id_cliente`),
  CONSTRAINT `fk_ventas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_ventas_turnos` FOREIGN KEY (`id_turno`) REFERENCES `turnos_caja` (`id_turno`) ON UPDATE CASCADE,
  CONSTRAINT `fk_ventas_usuarios` FOREIGN KEY (`id_cajero`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE,
  CONSTRAINT `fk_ventas_clientes` FOREIGN KEY (`id_cliente`) REFERENCES `clientes` (`id_cliente`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- detalle_ventas: lineas de cada venta
-- ------------------------------------------------------------
-- unidad_venta: 2026-09-25. Inferido: el resto. cantidad decimal porque se
-- venden fracciones (2.5 libras). productos RESTRICT (2026-09-23).
CREATE TABLE IF NOT EXISTS `detalle_ventas` (
  `id_detalle_venta` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_venta` bigint(20) UNSIGNED NOT NULL,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `cantidad` decimal(12,3) NOT NULL,
  `unidad_venta` varchar(30) DEFAULT NULL,
  `precio_unitario_historico` decimal(12,2) NOT NULL,
  `subtotal_linea` decimal(12,2) NOT NULL,
  PRIMARY KEY (`id_detalle_venta`),
  KEY `idx_detalle_venta` (`id_venta`),
  KEY `idx_detalle_producto` (`id_producto`),
  CONSTRAINT `fk_detalle_ventas` FOREIGN KEY (`id_venta`) REFERENCES `ventas` (`id_venta`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_detalle_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- abonos_fiados: pagos parciales de una venta fiada
-- ------------------------------------------------------------
-- id_turno, idx_abonos_turno, fk_abonos_turnos: 2026-09-23_abonos_id_turno.
-- Inferido: el resto. metodo_pago no tiene 'Mixto' (app/utils/helpers.py).
CREATE TABLE IF NOT EXISTS `abonos_fiados` (
  `id_abono` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_venta` bigint(20) UNSIGNED NOT NULL,
  `id_turno` bigint(20) UNSIGNED DEFAULT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `monto_abonado` decimal(12,2) NOT NULL,
  `metodo_pago` enum('Efectivo','Nequi/Daviplata','Tarjeta') NOT NULL DEFAULT 'Efectivo',
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_abono`),
  KEY `idx_abonos_tienda` (`id_tienda`),
  KEY `idx_abonos_venta` (`id_venta`),
  KEY `idx_abonos_turno` (`id_turno`),
  KEY `idx_abonos_usuario` (`id_usuario`),
  CONSTRAINT `fk_abonos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_abonos_ventas` FOREIGN KEY (`id_venta`) REFERENCES `ventas` (`id_venta`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_abonos_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE,
  CONSTRAINT `fk_abonos_turnos`
    FOREIGN KEY (`id_turno`) REFERENCES `turnos_caja` (`id_turno`)
    ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- gastos_caja: salidas de dinero (y pagos de jornada / cuentas por pagar)
-- ------------------------------------------------------------
-- fuente_dinero: 2026-09-23_gastos_fuente_base; monto_transferencia:
-- 2026-09-27. Inferido: el resto. usuarios RESTRICT (2026-09-23).
CREATE TABLE IF NOT EXISTS `gastos_caja` (
  `id_gasto` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_turno` bigint(20) UNSIGNED DEFAULT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `concepto` varchar(150) NOT NULL,
  `descripcion` varchar(255) DEFAULT NULL,
  `monto` decimal(12,2) NOT NULL,
  `monto_transferencia` decimal(12,2) NOT NULL DEFAULT 0.00,
  `fuente_dinero` enum('Caja Menor','Caja Fuerte','Bancos','Base') DEFAULT 'Bancos',
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_gasto`),
  KEY `idx_gastos_tienda_fecha` (`id_tienda`, `fecha_creacion`),
  KEY `idx_gastos_turno` (`id_turno`),
  KEY `idx_gastos_usuario` (`id_usuario`),
  CONSTRAINT `fk_gastos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_gastos_turnos` FOREIGN KEY (`id_turno`) REFERENCES `turnos_caja` (`id_turno`) ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_gastos_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- movimientos_inventario: entradas manuales de stock
-- ------------------------------------------------------------
-- stock_anterior/stock_posterior decimal: 2026-09-25; indices:
-- scripts/db_indexes. Inferido: el resto. usuarios RESTRICT (2026-09-23).
CREATE TABLE IF NOT EXISTS `movimientos_inventario` (
  `id_movimiento` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `tipo_movimiento` varchar(20) NOT NULL DEFAULT 'Entrada',
  `motivo` varchar(255) DEFAULT NULL,
  `cantidad` decimal(12,3) NOT NULL,
  `stock_anterior` decimal(12,3) DEFAULT NULL,
  `stock_posterior` decimal(12,3) DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_movimiento`),
  KEY `idx_movimientos_tienda` (`id_tienda`),
  KEY `idx_movimientos_inventario_fecha_creacion` (`fecha_creacion`),
  KEY `idx_movimientos_inventario_id_producto` (`id_producto`),
  KEY `idx_movimientos_usuario` (`id_usuario`),
  CONSTRAINT `fk_movimientos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_movimientos_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_movimientos_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- auditoria: bitacora de acciones (inferido)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `auditoria` (
  `id_auditoria` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED DEFAULT NULL,
  `id_usuario` bigint(20) UNSIGNED DEFAULT NULL,
  `accion` varchar(100) NOT NULL,
  `detalles` text DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_auditoria`),
  KEY `idx_auditoria_tienda_fecha` (`id_tienda`, `fecha_creacion`),
  KEY `idx_auditoria_usuario` (`id_usuario`),
  CONSTRAINT `fk_auditoria_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_auditoria_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- cuentas_por_pagar: 2026-09-22_cartera_b2b (exacta)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `cuentas_por_pagar` (
  `id_cuenta` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_proveedor` bigint(20) UNSIGNED DEFAULT NULL,
  `categoria` enum('Proveedor','Nomina','Servicios','Arriendo','Impuestos','Otro') NOT NULL DEFAULT 'Otro',
  `concepto` varchar(150) NOT NULL,
  `descripcion` varchar(255) DEFAULT NULL,
  `monto_total` decimal(12,2) NOT NULL,
  `monto_pagado` decimal(12,2) NOT NULL DEFAULT 0.00,
  `fecha_vencimiento` date DEFAULT NULL,
  `estado` enum('Pendiente','Pagada','Anulada') NOT NULL DEFAULT 'Pendiente',
  `id_usuario_creador` bigint(20) UNSIGNED DEFAULT NULL,
  `id_usuario_aprobador` bigint(20) UNSIGNED DEFAULT NULL,
  `fecha_ultimo_pago` timestamp NULL DEFAULT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_cuenta`),
  KEY `idx_cxp_tienda_estado` (`id_tienda`, `estado`, `fecha_vencimiento`),
  KEY `idx_cxp_proveedor` (`id_proveedor`),
  KEY `idx_cxp_creador` (`id_usuario_creador`),
  KEY `idx_cxp_aprobador` (`id_usuario_aprobador`),
  CONSTRAINT `fk_cxp_tiendas`
    FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`)
    ON DELETE CASCADE ON UPDATE CASCADE,
  CONSTRAINT `fk_cxp_proveedores`
    FOREIGN KEY (`id_proveedor`) REFERENCES `proveedores` (`id_proveedor`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_cxp_usuario_creador`
    FOREIGN KEY (`id_usuario_creador`) REFERENCES `usuarios` (`id_usuario`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `fk_cxp_usuario_aprobador`
    FOREIGN KEY (`id_usuario_aprobador`) REFERENCES `usuarios` (`id_usuario`)
    ON DELETE SET NULL ON UPDATE CASCADE,
  CONSTRAINT `chk_cxp_montos` CHECK (`monto_total` > 0 AND `monto_pagado` >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- master_movimientos: 2026-09-25_master_movimientos (exacta)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `master_movimientos` (
  `id_movimiento` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `tipo` enum('Ingreso','Gasto') NOT NULL,
  `concepto` varchar(150) NOT NULL,
  `monto` decimal(12,2) NOT NULL,
  `fecha` date NOT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_movimiento`),
  KEY `idx_master_mov_fecha` (`fecha`),
  CONSTRAINT `fk_master_mov_usuario` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- comisiones: 2026-10-06_comisiones_promotoras (exacta)
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `comisiones` (
  `id_comision` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_promotora` bigint(20) UNSIGNED NOT NULL,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `plan_id` varchar(20) NOT NULL,
  `meses` tinyint(3) UNSIGNED NOT NULL,
  `precio_plan` decimal(12,0) NOT NULL,
  `comision_base_seteo` decimal(12,0) NOT NULL,
  `bono_plan_15` decimal(12,0) NOT NULL,
  `comision_total` decimal(12,0) NOT NULL,
  `estado_pago` enum('pendiente','liquidado') NOT NULL DEFAULT 'pendiente',
  `fecha_registro` timestamp NOT NULL DEFAULT current_timestamp(),
  `fecha_liquidacion` datetime DEFAULT NULL,
  PRIMARY KEY (`id_comision`),
  KEY `idx_comisiones_promotora` (`id_promotora`, `estado_pago`, `fecha_registro`),
  KEY `idx_comisiones_tienda` (`id_tienda`),
  CONSTRAINT `fk_comisiones_promotora` FOREIGN KEY (`id_promotora`) REFERENCES `usuarios` (`id_usuario`),
  CONSTRAINT `fk_comisiones_tienda` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------
-- uso_diario: scripts/migracion_uso_diario (exacta). Sin FK a proposito.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `uso_diario` (
  `fecha` date NOT NULL,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `modulo` varchar(50) NOT NULL,
  `vistas` int(10) UNSIGNED NOT NULL DEFAULT 1,
  PRIMARY KEY (`fecha`, `id_tienda`, `id_usuario`, `modulo`),
  KEY `idx_uso_tienda_fecha` (`id_tienda`, `fecha`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;

-- ------------------------------------------------------------
-- Triggers
-- ------------------------------------------------------------
-- El volcado trae 7; este es el unico que el codigo permite reconstruir:
-- scripts/check_filtros_paginacion.py cuenta con que la fecha de un gasto la
-- fija el servidor aunque el INSERT mande otra. Los otros 6 hay que sacarlos
-- de produccion (SHOW TRIGGERS) o con el mysqldump del encabezado.
-- El cliente `mysql` entiende DELIMITER; otros clientes pueden necesitar
-- ejecutarlo como script.

DELIMITER $$
CREATE TRIGGER `bi_gastos_caja_fecha_creacion` BEFORE INSERT ON `gastos_caja`
FOR EACH ROW
BEGIN
  SET NEW.fecha_creacion = CURRENT_TIMESTAMP;
END$$
DELIMITER ;
