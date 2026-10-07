-- ============================================================
-- Migracion: pago Mixto en todas partes
-- Fecha: 2026-10-07
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- Va despues de 2026-10-07c_cuenta_dividida_llevar.sql. Mixto ya existia en
-- ventas.metodo_pago (cobro de una cuenta completa y cuenta dividida en
-- partes iguales que mezcla metodos). Ahora tambien se elige a mano:
--
-- 1) pedido_cuentas: cada persona de una cuenta dividida puede pagar Mixto.
--    Guarda cuanto dio en efectivo y cuanto por transferencia (NULL si no es
--    Mixto) para poder mostrarlo y cuadrar la caja.
-- 2) gastos_caja: `metodo_pago` del gasto. El monto en efectivo es
--    monto - monto_transferencia (como en jemPOS); solo esa parte sale del
--    cajon. NULL en gastos viejos.
--
-- Se puede correr varias veces.

-- 1) partes de una cuenta dividida -----------------------------------------
ALTER TABLE `pedido_cuentas`
  MODIFY COLUMN `metodo_pago` enum('Efectivo','Nequi/Daviplata','Tarjeta','Mixto') NOT NULL;

SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'pedido_cuentas' AND COLUMN_NAME = 'monto_efectivo'), 'DO 0', 'ALTER TABLE `pedido_cuentas` ADD COLUMN `monto_efectivo` decimal(12,2) DEFAULT NULL AFTER `metodo_pago`, ADD COLUMN `monto_transferencia` decimal(12,2) DEFAULT NULL AFTER `monto_efectivo`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;

-- 2) gastos --------------------------------------------------------------------
SET @s := IF(EXISTS(SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'gastos_caja' AND COLUMN_NAME = 'metodo_pago'), 'DO 0', 'ALTER TABLE `gastos_caja` ADD COLUMN `metodo_pago` enum(''Efectivo'',''Nequi/Daviplata'',''Tarjeta'',''Mixto'') DEFAULT NULL AFTER `monto_transferencia`');
PREPARE st FROM @s;
EXECUTE st;
DEALLOCATE PREPARE st;
