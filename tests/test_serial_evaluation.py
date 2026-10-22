from __future__ import annotations

import unittest

from test_bench_rollers.cli import evaluate
from test_bench_rollers.serial_gateway import decode_telemetry, link_result, mac_result


class SerialAndEvaluationTests(unittest.TestCase):
    def test_decodes_gateway_data_and_datax_vectors(self) -> None:
        data = (
            "5psf-lk!5yWZ3/p<!200Yo?{2:LW48]qY?Ul&U5Q(2*90ZE*DG!ZSCcNknc0g8tGWIQ]"
            "W0=LB3074vxppivF17:x87@]Yj77j[KPxwcf4<7CU2!&n10*1DGk"
        )
        datax = data + "3o}?kJR&IE@+JDUG&A+fBy:n<uhhQ1lkUl2u?3j3"
        decoded_data = decode_telemetry(f"<0x0399> [DATA]: {data}")
        decoded_datax = decode_telemetry(f"<0x0399> [DATAX]: {datax}")
        self.assertIsNotNone(decoded_data)
        self.assertIsNotNone(decoded_datax)
        assert decoded_data is not None and decoded_datax is not None
        self.assertEqual(decoded_data[0], "0x0399")
        self.assertEqual(decoded_data[1]["schema"], "roller-data-1.0")
        self.assertEqual(decoded_datax[1]["schema"], "roller-data-1.2")

    def test_link_confirmation_is_scoped_to_the_selected_node(self) -> None:
        self.assertIs(link_result("<0x0399> [LINK] linked", "0x0399"), True)
        self.assertIs(link_result("<0x0399> [LINK] Linked OK", "0x0399"), True)
        self.assertIsNone(link_result("<0x0398> [LINK] linked", "0x0399"))
        self.assertIs(link_result("<0x0399> [UNLINK] unlinked", "0x0399"), False)

    def test_mac_result_is_scoped_to_selected_mesh_and_normalized(self) -> None:
        self.assertEqual(
            mac_result("<0x0399> [MAC] aa:bb:cc:dd:ee:ff", "0x0399"),
            "AA:BB:CC:DD:EE:FF",
        )
        self.assertEqual(
            mac_result("<0x0399> [MAC]: aa:bb:cc:dd:ee:ff", "0x0399"),
            "AA:BB:CC:DD:EE:FF",
        )
        self.assertIsNone(mac_result("<0x0398> [MAC] aa:bb:cc:dd:ee:ff", "0x0399"))
        self.assertIsNone(mac_result("<0x0399> [MAC] unavailable", "0x0399"))

    def test_evaluation_applies_configured_limits_and_alarm_check(self) -> None:
        payload = {
            "fields": {
                "rpm": 500,
                "temperature": [25, None],
                "alarms": {"latched": {"all": 0}},
            }
        }
        self.assertEqual(evaluate(payload, rpm_min=400, rpm_max=600, temperature_max=80), (True, []))
        passed, failures = evaluate(payload, rpm_min=600)
        self.assertFalse(passed)
        self.assertTrue(any("RPM" in failure for failure in failures))
        payload["fields"]["alarms"] = {"latched": {"all": 2}}
        passed, failures = evaluate(payload)
        self.assertFalse(passed)
        self.assertTrue(any("Alarmas" in failure for failure in failures))
