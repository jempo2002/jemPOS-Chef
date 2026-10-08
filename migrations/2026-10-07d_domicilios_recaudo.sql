-- ============================================================
-- Migracion: domicilios y recaudo de domiciliarios
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07c_cuenta_dividida_llevar.sql (la "d" la ordena de
-- ultima). Un domicilio es un pedido sin mesa como el para llevar
-- (pedidos.tipo = 'domicilio', mismo uuid del dispositivo y numero por sede en
-- numero_llevar), con su direccion, el domiciliario que lo lleva y el control
-- del efectivo que ese domiciliario debe entregar al regresar.
--
-- 1) pedidos.tipo suma 'domicilio'. El CHECK tipo/id_mesa sigue igual: solo
--    'mesa' lleva mesa.
-- 2) domiciliarios: las personas que reparten, por sede. No son usuarios del
--    sistema (no entran ni gastan cupo del plan). Borrado suave
--    (estado_activo + nombre_vivo, como zonas y mesas).
-- 3) pedido_domicilios: una fila por domicilio (PK = id_pedido).
--    estado: por_despachar -> despachado (salio con el domiciliario) ->
--    entregado (el cliente lo recibio) -> liquidado (el cajero recibio la
--    plata). metodo_pago es como va a pagar el cliente: 'Efectivo' suma todo a
--    lo que el domiciliario debe entregar y 'Mixto' solo `monto_efectivo` (el
--    resto llega por transferencia). Si el pedido ya se cobro antes de
--    salir (pago anticipado), el domiciliario no cobra nada. Al liquidar un
--    domicilio sin cobrar se crea la venta (id_venta) en la caja abierta.
--    No se borran: son el comprobante de quien llevo que y cuanto entrego.
--    La FK a domiciliarios va sin CASCADE para que MariaDB deje el CHECK
--    (los ids nunca cambian).
-- 4) Indice del recaudo: (id_sede, estado, id_domiciliario). Lo que esta en
--    la calle es despachado/entregado; lo historico queda en 'liquidado' y el
--    indice ni lo toca, asi que la suma no se vuelve lenta con miles de
--    domicilios viejos.
-- 5) tiendas.tope_efectivo_domiciliario: desde cuanto efectivo en la calle se
--    marca en rojo a un domiciliario en Caja (por defecto $150.000).
--
-- Se puede correr varias veces.

-- 1) pedidos.tipo ------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedidos' AND COLUMN_NAME = 'tipo' AND COLUMN_TYPE LIKE '%domicilio%'), 'DO 0', 'ALTER TABLE `pedidos` MODIFY COLUMN `tipo` enum(''mesa'',''llevar'',''domicilio'') NOT NULL DEFAULT ''mesa''');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) domiciliarios -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `domiciliarios` (
  `id_domiciliario` bigint(20) UNSIGNED NOT NULL AUTO_INCREMENT,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `nombre` varchar(60) NOT NULL,
  `telefono` varchar(20) DEFAULT NULL,
  `estado_activo` tinyint(1) NOT NULL DEFAULT 1,
  `nombre_vivo` varchar(60) GENERATED ALWAYS AS (IF(`estado_activo` = 1, `nombre`, NULL)) STORED,
  `fecha_creacion` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_domiciliario`),
  UNIQUE KEY `uq_domiciliarios_nombre_vivo` (`id_sede`, `nombre_vivo`),
  KEY `idx_domiciliarios_sede_activo` (`id_sede`, `estado_activo`),
  CONSTRAINT `fk_domiciliarios_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_domiciliarios_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) y 4) pedido_domicilios ------------------------------------------------------
CREATE TABLE IF NOT EXISTS `pedido_domicilios` (
  `id_pedido` bigint(20) UNSIGNED NOT NULL,
  `id_tienda` bigint(20) UNSIGNED NOT NULL,
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `direccion` varchar(160) NOT NULL,
  `estado` enum('por_despachar','despachado','entregado','liquidado') NOT NULL DEFAULT 'por_despachar',
  `metodo_pago` enum('Efectivo','Nequi/Daviplata','Tarjeta','Mixto') NOT NULL DEFAULT 'Efectivo',
  `monto_efectivo` decimal(12,2) DEFAULT NULL,
  `paga_con` decimal(12,2) DEFAULT NULL,
  `id_domiciliario` bigint(20) UNSIGNED DEFAULT NULL,
  `despachado_en` datetime DEFAULT NULL,
  `id_usuario_despacha` bigint(20) UNSIGNED DEFAULT NULL,
  `entregado_en` datetime DEFAULT NULL,
  `liquidado_en` datetime DEFAULT NULL,
  `id_usuario_liquida` bigint(20) UNSIGNED DEFAULT NULL,
  `efectivo_recibido` decimal(12,2) DEFAULT NULL,
  `id_venta` bigint(20) UNSIGNED DEFAULT NULL,
  `creado_en` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id_pedido`),
  KEY `idx_pedido_domicilios_recaudo` (`id_sede`, `estado`, `id_domiciliario`),
  KEY `idx_pedido_domicilios_domiciliario` (`id_domiciliario`, `liquidado_en`),
  CONSTRAINT `fk_pedido_domicilios_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_domicilios_tiendas` FOREIGN KEY (`id_tienda`) REFERENCES `tiendas` (`id_tienda`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_domicilios_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_pedido_domicilios_domiciliarios` FOREIGN KEY (`id_domiciliario`) REFERENCES `domiciliarios` (`id_domiciliario`),
  CONSTRAINT `fk_pedido_domicilios_ventas` FOREIGN KEY (`id_venta`) REFERENCES `ventas` (`id_venta`) ON UPDATE CASCADE,
  CONSTRAINT `chk_pedido_domicilios_salio` CHECK (`estado` = 'por_despachar' OR `id_domiciliario` IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5) tope de efectivo por domiciliario ---------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tiendas' AND COLUMN_NAME = 'tope_efectivo_domiciliario'), 'DO 0', 'ALTER TABLE `tiendas` ADD COLUMN `tope_efectivo_domiciliario` decimal(12,2) NOT NULL DEFAULT 150000.00');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
