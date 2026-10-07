-- ============================================================
-- Migracion: cuenta dividida y pedidos para llevar
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07b_recetas_insumos.sql (la "c" la deja de ultima al
-- ordenar por nombre). Cierra la fase 3 del plan "Mesas, comandas y cuentas"
-- (dividir la cuenta) y los pedidos para llevar de la fase 4.
--
-- 1) pedidos: un pedido para llevar es un pedido sin mesa (`tipo` = 'llevar',
--    id_mesa NULL) con su numero por sede (`numero_llevar`), el nombre y el
--    telefono del cliente, y el uuid que pone el dispositivo
--    (`uuid_cliente`): asi se puede abrir y llenar sin conexion, igual que una
--    mesa, y reenviarlo desde la cola no crea dos. `entregado_en`: cuando se
--    le entrego al cliente (deja de verse en la lista de para llevar). Un
--    CHECK amarra tipo e id_mesa: una mesa siempre tiene mesa, un para llevar
--    nunca (id_mesa tiene FK sin CASCADE, asi que MariaDB si deja el CHECK).
-- 2) pedido_cuentas: las partes de una cuenta dividida, una fila por persona
--    con su monto, propina, metodo de pago y la venta que la cobro. `modo`
--    'items': cada parte es una venta propia con sus platos. 'iguales': una
--    sola venta con los platos reales y cada parte guarda su monto y metodo
--    (si mezclan efectivo con otro metodo, la venta queda Mixto). Se crean
--    al cobrar, en la misma transaccion que las ventas: no se editan ni se
--    borran (son el comprobante de quien pago que).
-- 3) pedido_cuenta_items: que linea (y cuanta cantidad) pago cada parte en el
--    modo 'items'. Una linea de 2 cervezas se puede partir 1 y 1.
--
-- Se puede correr varias veces.

-- 1) pedidos ---------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND COLUMN_NAME = 'tipo'), 'DO 0', 'ALTER TABLE `pedidos` ADD COLUMN `tipo` enum(''mesa'',''llevar'') NOT NULL DEFAULT ''mesa'' AFTER `id_sede`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND COLUMN_NAME = 'numero_llevar'), 'DO 0', 'ALTER TABLE `pedidos` ADD COLUMN `numero_llevar` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_mesa`, ADD COLUMN `cliente_nombre` varchar(80) DEFAULT NULL AFTER `numero_llevar`, ADD COLUMN `cliente_telefono` varchar(20) DEFAULT NULL AFTER `cliente_nombre`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND COLUMN_NAME = 'uuid_cliente'), 'DO 0', 'ALTER TABLE `pedidos` ADD COLUMN `uuid_cliente` char(36) DEFAULT NULL, ADD UNIQUE KEY `uq_pedidos_uuid_cliente` (`uuid_cliente`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND COLUMN_NAME = 'entregado_en'), 'DO 0', 'ALTER TABLE `pedidos` ADD COLUMN `entregado_en` datetime DEFAULT NULL AFTER `cerrado_en`, ADD KEY `idx_pedidos_sede_tipo` (`id_sede`, `tipo`, `estado`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND CONSTRAINT_NAME = 'chk_pedidos_tipo_mesa'), 'DO 0', 'ALTER TABLE `pedidos` ADD CONSTRAINT `chk_pedidos_tipo_mesa` CHECK ((`tipo` = ''mesa'') = (`id_mesa` IS NOT NULL))');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) partes de una cuenta dividida ----------------------------------------
CREATE TABLE IF NOT EXISTS `pedido_cuentas` (
  `id_cuenta` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_pedido` bigint(20) UNSIGNED NOT NULL,
  `numero` tinyint(3) UNSIGNED NOT NULL,
  `etiqueta` varchar(40) NOT NULL,
  `modo` enum('items','iguales') NOT NULL,
  `monto` decimal(12,2) NOT NULL,
  `propina` decimal(12,2) NOT NULL DEFAULT 0.00,
  `metodo_pago` enum('Efectivo','Nequi/Daviplata','Tarjeta') NOT NULL,
  `id_venta` bigint(20) UNSIGNED NOT NULL,
  `id_usuario` bigint(20) UNSIGNED NOT NULL,
  `creada_en` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_cuenta`),
  UNIQUE KEY `uq_pedido_cuentas_numero` (`id_pedido`, `numero`),
  KEY `idx_pedido_cuentas_venta` (`id_venta`),
  KEY `idx_pedido_cuentas_sede_fecha` (`id_sede`, `creada_en`),
  CONSTRAINT `fk_pedido_cuentas_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_cuentas_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_cuentas_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_cuentas_ventas` FOREIGN KEY (`id_venta`) REFERENCES `ventas` (`id_venta`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_cuentas_usuarios` FOREIGN KEY (`id_usuario`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) que pago cada parte (modo items) -------------------------------------
CREATE TABLE IF NOT EXISTS `pedido_cuenta_items` (
  `id_cuenta` bigint(20) UNSIGNED NOT NULL,
  `id_item` bigint(20) UNSIGNED NOT NULL,
  `cantidad` decimal(12,3) NOT NULL,
  PRIMARY KEY (`id_cuenta`, `id_item`),
  KEY `idx_pedido_cuenta_items_item` (`id_item`),
  CONSTRAINT `fk_pedido_cuenta_items_cuentas` FOREIGN KEY (`id_cuenta`) REFERENCES `pedido_cuentas` (`id_cuenta`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_cuenta_items_items` FOREIGN KEY (`id_item`) REFERENCES `pedido_items` (`id_item`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
