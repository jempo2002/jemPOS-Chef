-- ============================================================
-- Migracion: multisede sin tope de sedes en Completo y Cadena
-- Fecha: 2026-10-09
-- Motor: MariaDB y MySQL 8/9
-- ============================================================
--
-- jempo, 2026-10-09: en todos sus productos los planes con sedes traen 2
-- incluidas y se pueden abrir mas pagando cada una; las maneja el mismo Admin
-- y hay hasta 2 Admin. Rehace los triggers de 2026-10-07_sedes_planes_roles.sql:
-- Completo y Cadena ya no tienen tope (antes 4); el Basico sigue en 1. El
-- tope de Admin y el cobro viven en app/services/plan_service.py.
--
-- Se puede correr varias veces.

DROP TRIGGER IF EXISTS `bi_sedes_limite`;
DROP TRIGGER IF EXISTS `bu_sedes_limite`;

DELIMITER $$
CREATE TRIGGER `bi_sedes_limite` BEFORE INSERT ON `sedes` FOR EACH ROW
BEGIN
  DECLARE v_plan varchar(20);
  DECLARE v_activas int;
  IF NEW.`estado` = 'Activa' THEN
    SELECT `plan_id` INTO v_plan FROM `tiendas` WHERE `id_tienda` = NEW.`id_tienda`;
    SELECT COUNT(*) INTO v_activas FROM `sedes` WHERE `id_tienda` = NEW.`id_tienda` AND `estado` = 'Activa';
    IF COALESCE(v_plan, '') NOT IN ('completo', 'cadena') AND v_activas >= 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Limite de sedes del plan alcanzado';
    END IF;
  END IF;
END$$

CREATE TRIGGER `bu_sedes_limite` BEFORE UPDATE ON `sedes` FOR EACH ROW
BEGIN
  DECLARE v_plan varchar(20);
  DECLARE v_activas int;
  IF NEW.`estado` = 'Activa' AND (OLD.`estado` <> 'Activa' OR NEW.`id_tienda` <> OLD.`id_tienda`) THEN
    SELECT `plan_id` INTO v_plan FROM `tiendas` WHERE `id_tienda` = NEW.`id_tienda`;
    SELECT COUNT(*) INTO v_activas FROM `sedes`
      WHERE `id_tienda` = NEW.`id_tienda` AND `estado` = 'Activa' AND `id_sede` <> NEW.`id_sede`;
    IF COALESCE(v_plan, '') NOT IN ('completo', 'cadena') AND v_activas >= 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Limite de sedes del plan alcanzado';
    END IF;
  END IF;
END$$
DELIMITER ;
