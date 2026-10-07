-- ============================================================
-- Migracion: recetas, insumos y costo por plato
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07_turnos_mesas_comandas.sql (la "b" la deja de
-- ultima al ordenar por nombre). Es el plan "Recetas, insumos y costo por
-- plato": solo para los planes Completo y Cadena (la app lo controla con
-- requiere_funcion("recetas")).
--
-- 1) insumos: `stock_minimo_alerta` (alerta de stock bajo), costo con 4
--    decimales (un gramo de queso cuesta $18,5) y unidad base Gramo,
--    Mililitro o Unidad: la compra en kilo, libra o litro se convierte al
--    guardar, asi la receta y el descuento nunca convierten. `nombre_vivo` +
--    UNIQUE: no hay dos insumos activos con el mismo nombre (soft delete,
--    mismo truco que mesas). insumos.stock_actual queda sin uso: el stock es
--    por sede.
-- 2) stock_insumos_sedes: el stock de cada insumo en cada sede, igual que
--    stock_sedes para productos.
-- 3) recetas_productos: `estado_activo` (soft delete; volver a agregar el
--    mismo insumo reactiva la fila) y las FK pasan de CASCADE a RESTRICT:
--    nunca se pierde una receta por un borrado.
-- 4) movimientos_inventario: el kardex tambien guarda insumos. id_producto
--    pasa a nulo, `id_insumo` nuevo (uno de los dos, nunca ambos) e
--    `id_pedido` (de que cuenta salio el consumo).
-- 5) tiendas.margen_alerta_costo: la pantalla de Costos marca en rojo el
--    plato cuyos ingredientes pasan de este % del precio (35 por defecto).
-- 6) Triggers: el kardex no se edita ni se borra (ver chef-borrado-suave);
--    un error se corrige con un movimiento de Ajuste. Cada fila es de un
--    producto o de un insumo: va en trigger porque MariaDB no deja un CHECK
--    sobre columnas con FK ON UPDATE CASCADE.
--
-- Se puede correr varias veces.

-- 1) insumos ---------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'insumos' AND COLUMN_NAME = 'stock_minimo_alerta'), 'DO 0', 'ALTER TABLE `insumos` ADD COLUMN `stock_minimo_alerta` decimal(12,3) DEFAULT NULL AFTER `stock_actual`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

ALTER TABLE `insumos` MODIFY `costo_unitario` decimal(12,4) NOT NULL DEFAULT 0.0000;

UPDATE `insumos` SET `unidad_medida` = 'Unidad' WHERE `unidad_medida` NOT IN ('Gramo', 'Mililitro', 'Unidad');
ALTER TABLE `insumos` MODIFY `unidad_medida` enum('Gramo','Mililitro','Unidad') NOT NULL DEFAULT 'Unidad';

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'insumos' AND COLUMN_NAME = 'nombre_vivo'), 'DO 0', 'ALTER TABLE `insumos` ADD COLUMN `nombre_vivo` varchar(150) GENERATED ALWAYS AS (IF(`estado_activo` = 1, `nombre`, NULL)) STORED, ADD UNIQUE KEY `uq_insumos_nombre_vivo` (`id_tienda`, `nombre_vivo`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) stock de insumos por sede ---------------------------------------------
CREATE TABLE IF NOT EXISTS `stock_insumos_sedes` (
  `id_sede` bigint(20) UNSIGNED NOT NULL,
  `id_insumo` bigint(20) UNSIGNED NOT NULL,
  `stock_actual` decimal(12,3) NOT NULL DEFAULT 0.000,
  PRIMARY KEY (`id_sede`, `id_insumo`),
  KEY `idx_stock_insumos_insumo` (`id_insumo`),
  CONSTRAINT `fk_stock_insumos_sede` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE,
  CONSTRAINT `fk_stock_insumos_insumo` FOREIGN KEY (`id_insumo`) REFERENCES `insumos` (`id_insumo`) ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3) recetas ---------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'recetas_productos' AND COLUMN_NAME = 'estado_activo'), 'DO 0', 'ALTER TABLE `recetas_productos` ADD COLUMN `estado_activo` tinyint(1) NOT NULL DEFAULT 1, ADD KEY `idx_recetas_producto_activo` (`id_producto`, `estado_activo`)');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'recetas_productos' AND CONSTRAINT_NAME = 'fk_recetas_insumos' AND DELETE_RULE = 'CASCADE'), 'ALTER TABLE `recetas_productos` DROP FOREIGN KEY `fk_recetas_insumos`', 'DO 0');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'recetas_productos' AND CONSTRAINT_NAME = 'fk_recetas_insumos'), 'DO 0', 'ALTER TABLE `recetas_productos` ADD CONSTRAINT `fk_recetas_insumos` FOREIGN KEY (`id_insumo`) REFERENCES `insumos` (`id_insumo`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'recetas_productos' AND CONSTRAINT_NAME = 'fk_recetas_productos' AND DELETE_RULE = 'CASCADE'), 'ALTER TABLE `recetas_productos` DROP FOREIGN KEY `fk_recetas_productos`', 'DO 0');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.REFERENTIAL_CONSTRAINTS WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = 'recetas_productos' AND CONSTRAINT_NAME = 'fk_recetas_productos'), 'DO 0', 'ALTER TABLE `recetas_productos` ADD CONSTRAINT `fk_recetas_productos` FOREIGN KEY (`id_producto`) REFERENCES `productos` (`id_producto`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 4) kardex de insumos -----------------------------------------------------
ALTER TABLE `movimientos_inventario` MODIFY `id_producto` bigint(20) UNSIGNED DEFAULT NULL;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'movimientos_inventario' AND COLUMN_NAME = 'id_insumo'), 'DO 0', 'ALTER TABLE `movimientos_inventario` ADD COLUMN `id_insumo` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_producto`, ADD KEY `idx_movimientos_insumo_fecha` (`id_insumo`, `fecha_creacion`), ADD CONSTRAINT `fk_movimientos_insumos` FOREIGN KEY (`id_insumo`) REFERENCES `insumos` (`id_insumo`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'movimientos_inventario' AND COLUMN_NAME = 'id_pedido'), 'DO 0', 'ALTER TABLE `movimientos_inventario` ADD COLUMN `id_pedido` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_insumo`, ADD KEY `idx_movimientos_pedido` (`id_pedido`), ADD CONSTRAINT `fk_movimientos_pedidos` FOREIGN KEY (`id_pedido`) REFERENCES `pedidos` (`id_pedido`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 5) margen de alerta por tienda -------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'tiendas' AND COLUMN_NAME = 'margen_alerta_costo'), 'DO 0', 'ALTER TABLE `tiendas` ADD COLUMN `margen_alerta_costo` decimal(5,2) NOT NULL DEFAULT 35.00');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 6) kardex inmutable ------------------------------------------------------
DROP TRIGGER IF EXISTS `bu_movimientos_inmutable`;
DROP TRIGGER IF EXISTS `bd_movimientos_inmutable`;
DROP TRIGGER IF EXISTS `bi_movimientos_producto_o_insumo`;

DELIMITER $$
CREATE TRIGGER `bi_movimientos_producto_o_insumo` BEFORE INSERT ON `movimientos_inventario` FOR EACH ROW
BEGIN
  IF (NEW.`id_producto` IS NULL) = (NEW.`id_insumo` IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'El movimiento es de un producto o de un insumo';
  END IF;
END$$

CREATE TRIGGER `bu_movimientos_inmutable` BEFORE UPDATE ON `movimientos_inventario` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Los movimientos de inventario no se editan: registra un ajuste';
END$$

CREATE TRIGGER `bd_movimientos_inmutable` BEFORE DELETE ON `movimientos_inventario` FOR EACH ROW
BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Los movimientos de inventario no se borran: registra un ajuste';
END$$
DELIMITER ;
