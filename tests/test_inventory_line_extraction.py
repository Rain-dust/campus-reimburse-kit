import unittest

from core.inventory_line_extraction import (
    RecognizedLineItem,
    TextBox,
    extract_line_items_from_boxes,
)


class InventoryLineExtractionTests(unittest.TestCase):
    @staticmethod
    def _glyph_boxes(text, x, y, step):
        return [TextBox(character, x + index * step, y) for index, character in enumerate(text)]

    def test_extracts_and_reconciles_one_positioned_invoice_row(self):
        boxes = [
            TextBox("*导航遥控设备*RoboMaster C620无刷电机调速器", 10, 100),
            TextBox("C620", 260, 100),
            TextBox("件", 350, 100),
            TextBox("6", 430, 100),
            TextBox("161.00", 510, 100),
            TextBox("966.00", 610, 100),
        ]

        self.assertEqual(
            extract_line_items_from_boxes(boxes, 96_600),
            [
                RecognizedLineItem(
                    name="RoboMaster C620无刷电机调速器",
                    specification="C620",
                    unit="件",
                    quantity="6",
                    unit_price_cents=16_100,
                    amount_cents=96_600,
                    confidence="verified",
                )
            ],
        )

    def test_groups_boxes_with_nearby_y_coordinates_into_one_row(self):
        boxes = [
            TextBox("传感器", 10, 100),
            TextBox("个", 200, 102),
            TextBox("2", 260, 101),
            TextBox("50.00", 330, 99),
            TextBox("100.00", 430, 102),
        ]

        item = extract_line_items_from_boxes(boxes, 10_000)[0]

        self.assertEqual(item.name, "传感器")
        self.assertEqual(item.quantity, "2")
        self.assertEqual(item.unit_price_cents, 5_000)
        self.assertEqual(item.confidence, "verified")

    def test_marks_incomplete_row_pending_without_fabricating_a_price(self):
        boxes = [TextBox("电机", 10, 100), TextBox("件", 200, 100), TextBox("2", 260, 100)]

        item = extract_line_items_from_boxes(boxes, 10_000)[0]

        self.assertEqual(item.name, "电机")
        self.assertEqual(item.quantity, "2")
        self.assertIsNone(item.unit_price_cents)
        self.assertIsNone(item.amount_cents)
        self.assertEqual(item.confidence, "pending")

    def test_marks_all_rows_pending_when_line_amounts_do_not_match_receipt_total(self):
        boxes = [
            TextBox("编码器", 10, 200), TextBox("个", 200, 200), TextBox("1", 260, 200),
            TextBox("40.00", 330, 200), TextBox("40.00", 430, 200),
            TextBox("轴承", 10, 100), TextBox("个", 200, 100), TextBox("2", 260, 100),
            TextBox("30.00", 330, 100), TextBox("60.00", 430, 100),
        ]

        items = extract_line_items_from_boxes(boxes, 9_900)

        self.assertEqual([item.confidence for item in items], ["pending", "pending"])

    def test_reconciles_one_wrapped_discounted_item_from_invoice_columns(self):
        boxes = [
            TextBox("项目名称", 180, 590), TextBox("规格型号", 530, 590),
            TextBox("单位", 780, 590), TextBox("数量", 1090, 590),
            TextBox("单价", 1370, 590), TextBox("金额", 1670, 590),
            TextBox("*导航遥控设备*RoboMast", 50, 635),
            TextBox("件", 790, 633), TextBox("238.0533333333", 1184, 638),
            TextBox("6", 1140, 643),
            TextBox("1428.32", 1610, 633),
            TextBox("erC620无刷电机调速器", 50, 680),
            TextBox("*导航遥控设备*RoboMast", 50, 767),
            TextBox("-573.45", 1618, 764),
            TextBox("erC620 无刷电机调速器", 50, 811),
            TextBox("合计", 140, 1010),
        ]

        self.assertEqual(
            extract_line_items_from_boxes(boxes, 96_600),
            [
                RecognizedLineItem(
                    name="RoboMaster C620 无刷电机调速器",
                    specification="C620",
                    unit="件",
                    quantity="6",
                    unit_price_cents=16_100,
                    amount_cents=96_600,
                    confidence="verified",
                )
            ],
        )

    def test_reconciles_spaced_headers_and_one_taxed_item(self):
        boxes = [
            TextBox("项目名称", 45.43, 158.79), TextBox("规格型号", 119.41, 158.79),
            TextBox("单 位", 189.99, 158.79), TextBox("数 量", 263.69, 158.79),
            TextBox("单 价", 334.56, 158.79), TextBox("金 额", 406.84, 158.79),
            TextBox("*风机风扇*排烟机", 12.76, 167.98),
            TextBox("排烟机", 119.05, 168.66), TextBox("件", 198.17, 168.66),
            TextBox("1", 286.05, 168.66), TextBox("168.514851485149", 297.41, 168.30),
            TextBox("168.51", 406.69, 168.66), TextBox("1%", 467.46, 168.66),
            TextBox("1.69", 564.51, 168.66), TextBox("计", 103.54, 269.34),
            TextBox("合 计", 140.0, 287.76),
        ]

        self.assertEqual(
            extract_line_items_from_boxes(boxes, 17_020),
            [
                RecognizedLineItem(
                    name="排烟机",
                    specification="排烟机",
                    unit="件",
                    quantity="1",
                    unit_price_cents=17_020,
                    amount_cents=17_020,
                    confidence="verified",
                )
            ],
        )

    def test_reconciles_wrapped_discounted_item_and_ignores_hidden_number(self):
        boxes = [
            TextBox("项目名称", 47.19, 155.50), TextBox("规格型号", 133.65, 155.50),
            TextBox("单位", 193.67, 155.50), TextBox("数量", 272.55, 155.50),
            TextBox("单价", 343.41, 155.50), TextBox("金额", 417.11, 155.50),
            TextBox("件", 198.17, 166.84), TextBox("6", 286.05, 166.84),
            TextBox("1428.32", 403.61, 166.84),
            TextBox("*导航遥控设备*RoboMast", 12.75, 177.64),
            TextBox("er C620 无刷电机调速器", 12.75, 188.44), TextBox("2", 12.75, 188.44),
            TextBox("-573.45", 403.61, 199.24),
            TextBox("*导航遥控设备*RoboMast", 12.75, 210.04),
            TextBox("er C620 无刷电机调速器", 12.75, 220.84), TextBox("2", 12.75, 220.84),
            TextBox("合               计", 140.0, 263.22),
        ]

        item = extract_line_items_from_boxes(boxes, 96_600)[0]

        self.assertEqual(item.name, "RoboMaster C620 无刷电机调速器")
        self.assertEqual((item.specification, item.quantity), ("C620", "6"))
        self.assertEqual((item.unit_price_cents, item.amount_cents), (16_100, 96_600))
        self.assertEqual(item.confidence, "verified")

    def test_reassembles_character_level_invoice_boxes_and_supports_strip_unit(self):
        boxes = [
            *self._glyph_boxes("项目名称", 45.43, 158.79, 9),
            *self._glyph_boxes("规格型号", 119.41, 158.79, 9),
            *self._glyph_boxes("单位", 190.00, 158.79, 18),
            *self._glyph_boxes("数量", 263.70, 158.79, 18),
            *self._glyph_boxes("单价", 334.56, 158.79, 18),
            *self._glyph_boxes("金额", 406.85, 158.79, 18),
            *self._glyph_boxes("*计算机配套产品*内存条", 12.76, 167.97, 9),
            *self._glyph_boxes("188.118811881188", 297.42, 168.29, 4),
            TextBox("条", 198.18, 168.65),
            TextBox("1", 286.05, 168.65),
            *self._glyph_boxes("188.12", 406.70, 168.65, 4.5),
            *self._glyph_boxes("价税合计（大写）", 48.00, 287.76, 9),
        ]

        self.assertEqual(
            extract_line_items_from_boxes(boxes, 19_000),
            [
                RecognizedLineItem(
                    name="内存条",
                    unit="条",
                    quantity="1",
                    unit_price_cents=19_000,
                    amount_cents=19_000,
                    confidence="verified",
                )
            ],
        )

    def test_reconciles_one_cent_rounded_unit_price_and_multiline_specification(self):
        boxes = [
            TextBox("项目名称", 44.36, 158.90),
            TextBox("规格型号", 118.49, 158.90),
            TextBox("单位", 189.18, 158.90),
            TextBox("数量", 262.88, 158.90),
            TextBox("单价", 332.33, 158.90),
            TextBox("金额", 406.03, 158.90),
            TextBox("*电子计算机*电脑*超小", 12.76, 169.60),
            TextBox("NUC11PHI7C 幻", 120.47, 169.60),
            TextBox("台", 198.18, 169.60),
            TextBox("3", 286.05, 169.60),
            TextBox("758.745874587458", 297.42, 169.24),
            TextBox("2276.24", 402.20, 169.60),
            TextBox("型计算机产品", 12.76, 182.00),
            TextBox("影峡谷 i7 1165", 120.47, 182.00),
            TextBox("G7+RTX2060 6G", 120.47, 194.40),
            TextBox("独显", 120.47, 206.80),
            TextBox("价税合计（大写）", 47.20, 286.70),
        ]

        self.assertEqual(
            extract_line_items_from_boxes(boxes, 229_900),
            [
                RecognizedLineItem(
                    name="电脑 超小型计算机产品",
                    specification="NUC11PHI7C 幻影峡谷 i7 1165G7+RTX2060 6G独显",
                    unit="台",
                    quantity="3",
                    unit_price_cents=76_633,
                    amount_cents=229_900,
                    confidence="verified",
                )
            ],
        )

    def test_normalizes_character_spaced_model_specification(self):
        boxes = [
            TextBox("*金属制品*轴承", 12.76, 168.66),
            TextBox("J A 0 2 0 X P O", 119.05, 168.66),
            TextBox("个", 198.17, 168.66),
            TextBox("1", 286.05, 168.66),
            TextBox("186.70", 334.56, 168.66),
            TextBox("186.70", 406.69, 168.66),
        ]

        item = extract_line_items_from_boxes(boxes, 18_670)[0]

        self.assertEqual(item.name, "轴承")
        self.assertEqual(item.specification, "JA020XPO")
        self.assertEqual(item.confidence, "verified")


if __name__ == "__main__":
    unittest.main()
