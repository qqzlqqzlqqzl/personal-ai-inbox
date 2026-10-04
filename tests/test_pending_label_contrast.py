"""Rejection controls for real-browser color records; no browser is launched."""
import math
import unittest

from console_link_contrast import contrast
from pending_label_contrast import validate_measurement


def observation(foreground=(78, 89, 105), background=(255, 255, 255), theme='light'):
    return {'tag': 'P', 'theme': theme, 'disabled': False, 'font_size': '12px',
            'text': '发布方标注非免费，访问条件待核实；暂不推荐',
            'rect': {'width': 270, 'height': 18}, 'foreground': list(foreground),
            'effective_background': list(background), 'contrast': contrast(foreground, background)}


class PendingContrastControls(unittest.TestCase):
    def test_theme_color_examples_above_ordinary_text_threshold(self):
        for row in [observation(), observation(background=(242,243,245)),
                    observation((189,189,190), (35,35,36), 'dark')]:
            with self.subTest(row=row):
                self.assertGreaterEqual(validate_measurement(row, row['theme']), 4.5)

    def test_actual_old_light_token_rejected_on_both_observed_backgrounds(self):
        for background in ((255,255,255), (242,243,245)):
            row=observation((134,144,156), background)
            self.assertLess(row['contrast'], 4.5)
            with self.assertRaisesRegex(AssertionError, 'insufficient contrast'):
                validate_measurement(row, 'light')

    def test_spoofed_ratio_cannot_hide_low_actual_color_contrast(self):
        row=observation((134,144,156)); row['contrast']=7
        with self.assertRaisesRegex(AssertionError, 'inconsistent'):
            validate_measurement(row, 'light')

    def test_information_cannot_use_disabled_or_large_text_exemption(self):
        for key,value in [('disabled',True),('font_size','24px'),('text',''),('tag','BUTTON'),('theme','dark')]:
            row=observation();row[key]=value
            with self.subTest(key=key),self.assertRaises(AssertionError):
                validate_measurement(row,'light')

    def test_unknown_theme_is_not_a_valid_same_theme_record(self):
        with self.assertRaisesRegex(AssertionError, 'unsupported measured theme'):
            validate_measurement(observation(theme='sepia'), 'sepia')

    def test_missing_positive_geometry_rejected(self):
        for key in ['width','height']:
            row=observation();row['rect'][key]=0
            with self.subTest(key=key),self.assertRaises(AssertionError):
                validate_measurement(row,'light')

    def test_composed_colors_require_finite_rgb_channels(self):
        for color in ([0,0], [0,0,0,1], [True,0,0], [-1,0,0], [256,0,0], [math.nan,0,0], [math.inf,0,0]):
            for key in ['foreground','effective_background']:
                row=observation();row[key]=color
                with self.subTest(key=key,color=color),self.assertRaises(AssertionError):
                    validate_measurement(row,'light')

    def test_nonfinite_or_falsified_record_ratio_rejected(self):
        for ratio in [math.nan,math.inf,4.499,21]:
            row=observation();row['contrast']=ratio
            with self.subTest(ratio=ratio),self.assertRaises(AssertionError):
                validate_measurement(row,'light')


if __name__=='__main__':
    unittest.main()
