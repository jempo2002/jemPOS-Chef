-- ============================================================
-- Migracion: carta, caja por sede, mesas, pedidos y comandas
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07_sedes_planes_roles.sql. Es el plan "Mesas,
-- comandas y cuentas" (fases 1 y 2): el pedido vive abierto por mesa, el
-- inventario se descuenta al ENVIAR A COCINA (regla de jempo, 2026-10-07) y el
-- cobro crea una venta normal con la propina aparte.
--
-- 1) productos: `estacion` (cocina, bar o ninguna: una gaseosa no genera
--    comanda) y `controla_stock` (0 = plato que se prepara sin inventario;
--    las recetas llegan en el modulo siguiente).
-- 2) stock_sedes: el stock de cada producto en cada sede. Antes vivia en
--    productos.stock_actual, que es uno solo por restaurante y no sirve con
--    varias sedes. productos.stock_actual queda sin uso.
-- 3) movimientos_inventario.id_sede: el kardex es por sede. Nunca se borra ni
--    se edita (ver chef-borrado-suave): se corrige con ajustes.
-- 4) turnos_caja.id_sede: una caja abierta por sede como maximo
--    (`sede_abierta` + UNIQUE, mismo truco que sedes.nombre_vivo).
-- 5) ventas: id_sede, id_pedido, id_mesero, propina y uuid_cliente (el id que
--    pone el dispositivo: si el cobro se reenvia desde la cola sin conexion,
--    no se cobra dos veces). La propina NO entra en subtotal ni total_final.
-- 6) consecutivos: numero de venta y de comanda por tienda/sede sin carreras
--    (jemPOS usaba COUNT(*) y dos cobros a la vez sacaban el mismo numero).
-- 7) zonas, mesas, pedidos, pedido_items, comandas y mermas. Soft delete en
--    zonas y mesas (estado_activo + nombre_vivo). Un pedido abierto por mesa:
--    `mesa_ocupada` + UNIQUE. MariaDB no deja una columna generada sobre una
--    FK con ON UPDATE CASCADE: por eso pedidos.id_mesa y turnos_caja.id_sede
--    llevan FK sin CASCADE (los ids nunca cambian). Lo anulado despues de enviar a cocina queda en
--    mermas (el inventario ya salio).
--
-- Se puede correr varias veces.

-- 1) productos -------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'productos' AND COLUMN_NAME = 'estacion'), 'DO 0', 'ALTER TABLE `productos` ADD COLUMN `estacion` enum(''cocina'',''bar'',''ninguna'') NOT NULL DEFAULT ''cocina'' AFTER `tipo`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'productos' AND COLUMN_NAME = 'controla_stock'), 'DO 0', 'ALTER TABLE `productos` ADD COLUMN `controla_stock` tinyint(1) NOT NULL DEFAULT 0 AFTER `estacion`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) stock por sede --------------------------------------------------------
CREATE TABLE IF NOT EXISTS `stock_sedes` (
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `stock_actual` decimal(12,3) NOT NULL DEFAULT 0.000,
  `stock_minimo` decimal(12,3) DEFAULT NULL,
  PRIMARY KEY (`id_sede`, `id_producto`),
  KEY `idx_stock_sedes_producto` (`id_producto`),
  CONSTRAINT `fk_stock_sedes_sede` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_stock_sedes_producto` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) kardex por sede -------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'movimientos_inventario' AND COLUMN_NAME = 'id_sede'), 'DO 0', 'ALTER TABLE `movimientos_inventario` ADD COLUMN `id_sede` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_tienda`, ADD KEY `idx_movimientos_sede_fecha` (`id_sede`, `fecha_creacion`), ADD CONSTRAINT `fk_movimientos_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 4) caja por sede ---------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'turnos_caja' AND COLUMN_NAME = 'id_sede'), 'DO 0', 'ALTER TABLE `turnos_caja` ADD COLUMN `id_sede` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_tienda`, ADD CONSTRAINT `fk_turnos_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

UPDATE `turnos_caja` t
JOIN `sedes` s ON s.`id_tienda` = t.`id_tienda` AND s.`es_principal` = 1
SET t.`id_sede` = s.`id_sede`
WHERE t.`id_sede` IS NULL;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'turnos_caja' AND COLUMN_NAME = 'sede_abierta'), 'DO 0', 'ALTER TABLE `turnos_caja` ADD COLUMN `sede_abierta` bigint(20) UNSIGNED GENERATED ALWAYS AS (IF(`estado_turno` = ''Abierto'', `id_sede`, NULL)) STORED, ADD UNIQUE KEY `uq_turnos_sede_abierta` (`sede_abierta`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 6) consecutivos (antes de ventas: el relleno no lo necesita) -------------
CREATE TABLE IF NOT EXISTS `consecutivos` (
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `clave` varchar(40) NOT NULL,
  `ultimo` bigint(20) UNSIGNED NOT NULL DEFAULT 0,
  PRIMARY KEY (`id_tienda`, `clave`),
  CONSTRAINT `fk_consecutivos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7) zonas, mesas y pedidos ------------------------------------------------
CREATE TABLE IF NOT EXISTS `zonas` (
  `id_zona` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(60) NOT NULL,
  `orden` smallint(6) NOT NULL DEFAULT 0,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `nombre_vivo` varchar(60) GENERATED ALWAYS AS (IF(`estado_activo` = 1, `nombre`, NULL)) STORED,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_zona`),
  UNIQUE KEY `uq_zonas_nombre_vivo` (`id_sede`, `nombre_vivo`),
  KEY `idx_zonas_tienda` (`id_tienda`),
  CONSTRAINT `fk_zonas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_zonas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `mesas` (
  `id_mesa` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_zona` bigint(20) UNSIGNED DEFAULT NULL,
  `nombre` varchar(30) NOT NULL,
  `capacidad` tinyint(3) UNSIGNED NOT NULL DEFAULT 4,
  `orden` smallint(6) NOT NULL DEFAULT 0,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `nombre_vivo` varchar(30) GENERATED ALWAYS AS (IF(`estado_activo` = 1, `nombre`, NULL)) STORED,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_mesa`),
  UNIQUE KEY `uq_mesas_nombre_vivo` (`id_sede`, `nombre_vivo`),
  KEY `idx_mesas_tienda` (`id_tienda`),
  KEY `idx_mesas_zona` (`id_zona`),
  CONSTRAINT `fk_mesas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_mesas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_mesas_zonas` FOREIGN KEY (`id_zona`) REFERENCES `zonas` (`id_zona`) ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `pedidos` (
  `id_pedido` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_mesa` bigint(20) UNSIGNED DEFAULT NULL,
  `id_mesero` bigint(20) UNSIGNED NOT NULL,
  `comensales` tinyint(3) UNSIGNED DEFAULT NULL,
  `estado` enum('abierto','por_cobrar','cerrado','anulado') NOT NULL DEFAULT 'abierto',
  `notas` varchar(255) DEFAULT NULL,
  `abierto_en` timestamp NOT NULL DEFAULT current_timestamp(),
  `cerrado_en` datetime DEFAULT NULL,
  `id_usuario_cierre` bigint(20) UNSIGNED DEFAULT NULL,
  `motivo_anulacion` varchar(255) DEFAULT NULL,
  `mesa_ocupada` bigint(20) UNSIGNED GENERATED ALWAYS AS (IF(`estado` IN ('abierto', 'por_cobrar'), `id_mesa`, NULL)) STORED,
  PRIMARY KEY (`id_pedido`),
  UNIQUE KEY `uq_pedidos_mesa_ocupada` (`mesa_ocupada`),
  KEY `idx_pedidos_sede_estado` (`id_sede`, `estado`),
  KEY `idx_pedidos_tienda_fecha` (`id_tienda`, `abierto_en`),
  KEY `idx_pedidos_mesa` (`id_mesa`),
  KEY `idx_pedidos_mesero` (`id_mesero`),
  CONSTRAINT `fk_pedidos_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedidos_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedidos_mesas` FOREIGN KEY (`id_mesa`) REFERENCES `mesas` (`id_mesa`),
  CONSTRAINT `fk_pedidos_mesero` FOREIGN KEY (`id_mesero`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `comandas` (
  `id_comanda` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_pedido` bigint(20) UNSIGNED NOT NULL,
  `numero` bigint(20) UNSIGNED NOT NULL,
  `estacion` enum('cocina','bar') NOT NULL,
  `estado` enum('nueva','preparando','lista','entregada') NOT NULL DEFAULT 'nueva',
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `creada_en` timestamp NOT NULL DEFAULT current_timestamp(),
  `lista_en` datetime DEFAULT NULL,
  `actualizada_en` timestamp(3) NOT NULL DEFAULT current_timestamp(3) ON UPDATE current_timestamp(3),
  PRIMARY KEY (`id_comanda`),
  KEY `idx_comandas_sede_estado` (`id_sede`, `estado`, `actualizada_en`),
  KEY `idx_comandas_pedido` (`id_pedido`),
  CONSTRAINT `fk_comandas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_comandas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_comandas_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE,
  CONSTRAINT `fk_comandas_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `pedido_items` (
  `id_item` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_pedido` bigint(20) UNSIGNED NOT NULL,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `cantidad` decimal(12,3) NOT NULL,
  `precio_unitario` decimal(12,2) NOT NULL,
  `nota` varchar(150) DEFAULT NULL,
  `estado` enum('pendiente','enviado','listo','entregado','anulado') NOT NULL DEFAULT 'pendiente',
  `id_comanda` bigint(20) UNSIGNED DEFAULT NULL,
  `uuid_cliente` char(36) DEFAULT NULL,
  `creado_por` bigint(20) UNSIGNED NOT NULL,
  `creado_en` timestamp NOT NULL DEFAULT current_timestamp(),
  `anulado_por` bigint(20) UNSIGNED DEFAULT NULL,
  `anulado_en` datetime DEFAULT NULL,
  `motivo_anulacion` varchar(255) DEFAULT NULL,
  PRIMARY KEY (`id_item`),
  UNIQUE KEY `uq_pedido_items_uuid` (`uuid_cliente`),
  KEY `idx_pedido_items_pedido` (`id_pedido`, `estado`),
  KEY `idx_pedido_items_comanda` (`id_comanda`),
  KEY `idx_pedido_items_producto` (`id_producto`),
  CONSTRAINT `fk_pedido_items_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_items_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_items_comandas` FOREIGN KEY (`id_comanda`) REFERENCES `comandas` (`id_comanda`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS `mermas` (
  `id_merma` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_item` bigint(20) UNSIGNED DEFAULT NULL,
  `id_producto` bigint(20) UNSIGNED NOT NULL,
  `cantidad` decimal(12,3) NOT NULL,
  `costo_unitario` decimal(12,2) NOT NULL DEFAULT 0.00,
  `motivo` varchar(255) NOT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `creada_en` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_merma`),
  KEY `idx_mermas_sede_fecha` (`id_sede`, `creada_en`),
  KEY `idx_mermas_tienda` (`id_tienda`),
  CONSTRAINT `fk_mermas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_mermas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_mermas_items` FOREIGN KEY (`id_item`) REFERENCES `pedido_items` (`id_item`) ON UPDATE CASCADE,
  CONSTRAINT `fk_mermas_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5) ventas ----------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ventas' AND COLUMN_NAME = 'id_sede'), 'DO 0', 'ALTER TABLE `ventas` ADD COLUMN `id_sede` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_tienda`, ADD KEY `idx_ventas_sede_fecha` (`id_sede`, `fecha_creacion`), ADD CONSTRAINT `fk_ventas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ventas' AND COLUMN_NAME = 'id_pedido'), 'DO 0', 'ALTER TABLE `ventas` ADD COLUMN `id_pedido` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_cliente`, ADD COLUMN `id_mesero` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_pedido`, ADD KEY `idx_ventas_pedido` (`id_pedido`), ADD KEY `idx_ventas_mesero` (`id_mesero`), ADD CONSTRAINT `fk_ventas_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE, ADD CONSTRAINT `fk_ventas_mesero` FOREIGN KEY (`id_mesero`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ventas' AND COLUMN_NAME = 'propina'), 'DO 0', 'ALTER TABLE `ventas` ADD COLUMN `propina` decimal(12,2) NOT NULL DEFAULT 0.00 AFTER `total_final`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'ventas' AND COLUMN_NAME = 'uuid_cliente'), 'DO 0', 'ALTER TABLE `ventas` ADD COLUMN `uuid_cliente` char(36) DEFAULT NULL, ADD UNIQUE KEY `uq_ventas_uuid_cliente` (`uuid_cliente`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

UPDATE `ventas` v
JOIN `sedes` s ON s.`id_tienda` = v.`id_tienda` AND s.`es_principal` = 1
SET v.`id_sede` = s.`id_sede`
WHERE v.`id_sede` IS NULL;
