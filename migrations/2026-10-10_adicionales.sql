-- ============================================================
-- Migracion: adicionales (salsas, extras) con receta propia
-- Fecha: 2026-10-10
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-09_contabilidad_reportes.sql. Se puede correr varias
-- veces.
--
-- 1) productos.es_adicional: la salsa (o el extra de queso, la porcion de
--    papa) es un producto de la carta que no se pide solo: se le agrega a un
--    plato al tomar el pedido. Su precio es lo que cobra como adicional (0 =
--    va gratis pero igual descuenta inventario). Puede tener receta propia
--    (recetas_productos, planes Completo y Cadena) o llevar inventario.
-- 2) pedido_items.id_item_padre: el adicional es una linea mas del pedido,
--    amarrada al plato al que se le agrego. Asi descuenta inventario al
--    enviar a cocina, sale en la comanda debajo de su plato, se cobra, entra
--    en la venta, en los reportes y en la cuenta dividida como cualquier
--    linea. Anular el plato anula sus adicionales.

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'productos' AND COLUMN_NAME = 'es_adicional'), 'DO 0', 'ALTER TABLE `productos` ADD COLUMN `es_adicional` tinyint(1) NOT NULL DEFAULT 0 AFTER `es_preparado`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedido_items' AND COLUMN_NAME = 'id_item_padre'), 'DO 0', 'ALTER TABLE `pedido_items` ADD COLUMN `id_item_padre` bigint(20) UNSIGNED DEFAULT NULL AFTER `id_producto`, ADD KEY `idx_pedido_items_padre` (`id_item_padre`), ADD CONSTRAINT `fk_pedido_items_padre` FOREIGN KEY (`id_item_padre`) REFERENCES `pedido_items` (`id_item`) ON UPDATE CASCADE');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
