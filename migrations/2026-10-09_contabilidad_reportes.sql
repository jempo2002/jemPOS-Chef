-- ============================================================
-- Migracion: contabilidad y reportes
-- Fecha: 2026-10-09
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07e_pago_mixto.sql. Se puede correr varias veces.
--
-- 1) detalle_ventas.costo_unitario_historico: lo que costaba el plato (su
--    receta o el costo de la carta) en el momento de cobrar. La utilidad de
--    un mes viejo no cambia cuando sube el precio de un insumo. Las ventas de
--    antes de esta migracion toman el costo actual del producto.
-- 2) gastos_caja:
--    - id_sede: de que sede es el gasto (los del turno toman la sede del
--      turno; los viejos sin turno, la sede principal).
--    - categoria: para el estado de resultados. 'insumos' (compras de
--      ingredientes y mercancia) no se resta como gasto: ese dinero se vuelve
--      costo cuando el plato se vende. Se muestra aparte, en flujo de caja.
--    - estado_activo + anulado_*: un gasto mal registrado se anula (borrado
--      suave) con motivo; nunca se borra.

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'detalle_ventas' AND COLUMN_NAME = 'costo_unitario_historico'), 'DO 0', 'ALTER TABLE `detalle_ventas` ADD COLUMN `costo_unitario_historico` decimal(12,4) DEFAULT NULL AFTER `precio_unitario_historico`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

UPDATE `detalle_ventas` dv
JOIN `productos` p ON p.`id_producto` = dv.`id_producto`
SET dv.`costo_unitario_historico` = p.`precio_costo`
WHERE dv.`costo_unitario_historico` IS NULL;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gastos_caja' AND COLUMN_NAME = 'id_sede'), 'DO 0', 'ALTER TABLE `gastos_caja` ADD COLUMN `id_sede` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_tienda`, ADD KEY `idx_gastos_sede_fecha` (`id_sede`, `fecha_creacion`), ADD CONSTRAINT `fk_gastos_sedes` FOREIGN KEY (`id_sede`) REFERENCES `sedes` (`id_sede`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

UPDATE `gastos_caja` g
JOIN `turnos_caja` t ON t.`id_turno` = g.`id_turno`
SET g.`id_sede` = t.`id_sede`
WHERE g.`id_sede` IS NULL AND t.`id_sede` IS NOT NULL;

UPDATE `gastos_caja` g
JOIN `sedes` s ON s.`id_tienda` = g.`id_tienda` AND s.`es_principal` = 1
SET g.`id_sede` = s.`id_sede`
WHERE g.`id_sede` IS NULL;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gastos_caja' AND COLUMN_NAME = 'categoria'), 'DO 0', 'ALTER TABLE `gastos_caja` ADD COLUMN `categoria` enum(''insumos'',''nomina'',''arriendo'',''servicios'',''transporte'',''mantenimiento'',''impuestos'',''publicidad'',''otros'') NOT NULL DEFAULT ''otros'' AFTER `concepto`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gastos_caja' AND COLUMN_NAME = 'estado_activo'), 'DO 0', 'ALTER TABLE `gastos_caja` ADD COLUMN `estado_activo` tinyint(1) NOT NULL DEFAULT 1, ADD COLUMN `anulado_por` bigint(20) UNSIGNED DEFAULT NULL, ADD COLUMN `anulado_en` datetime DEFAULT NULL, ADD COLUMN `motivo_anulacion` varchar(255) DEFAULT NULL, ADD KEY `idx_gastos_tienda_activo_fecha` (`id_tienda`, `estado_activo`, `fecha_creacion`), ADD CONSTRAINT `fk_gastos_anulado_por` FOREIGN KEY (`anulado_por`) REFERENCES `usuarios` (`id_usuario`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
