# 표시 폭 계산과 줄 단위 diff 렌더러를 확인하는 테스트

import unittest

from mdirnet import config
from mdirnet.ui import screen, term


class TestWidth(unittest.TestCase):
    def test_ascii_is_one_cell_each(self):
        self.assertEqual(screen.width("hello"), 5)

    def test_hangul_is_two_cells_each(self):
        self.assertEqual(screen.width("한글"), 4)
        self.assertEqual(screen.width("보고서.txt"), 3 * 2 + 4)

    def test_ansi_sequences_do_not_count(self):
        self.assertEqual(screen.width(term.BOLD + "abc" + term.RESET), 3)
        self.assertEqual(screen.width(term.BAR + "한글" + term.RESET), 4)

    def test_empty(self):
        self.assertEqual(screen.width(""), 0)


class TestFit(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(screen.fit("abc", 10), "abc")

    def test_truncates_with_ascii_marker(self):
        """생략 표시는 ASCII 여야 한다. '…' 는 Ambiguous 폭이라 한글 터미널에서
        2칸으로 그려져 줄 전체를 1칸 밀어버린다."""
        out = screen.fit("abcdefghij", 5)
        self.assertEqual(screen.width(out), 5)
        self.assertTrue(out.endswith("~"))
        self.assertTrue(out.isascii())

    def test_never_splits_a_wide_char_in_half(self):
        """한글을 반 칸만 남기고 자르면 테두리가 어긋난다."""
        for limit in range(1, 12):
            self.assertLessEqual(screen.width(screen.fit("가나다라마", limit)), limit)

    def test_zero_limit(self):
        self.assertEqual(screen.fit("abc", 0), "")


class TestFitLeft(unittest.TestCase):
    """경로는 뒤를 남겨야 한다. 앞을 남기면 지금 폴더 이름이 사라진다."""

    def test_short_text_untouched(self):
        self.assertEqual(screen.fit_left("/short", 30), "/short")

    def test_keeps_the_tail_and_marks_the_cut(self):
        out = screen.fit_left("/private/var/folders/31/abc/Downloads", 20)
        self.assertTrue(out.endswith("Downloads"))
        self.assertTrue(out.startswith("~"))
        self.assertEqual(screen.width(out), 20)

    def test_exact_width_with_hangul(self):
        for limit in range(2, 20):
            self.assertLessEqual(screen.width(screen.fit_left("/한글/경로/문서", limit)), limit)

    def test_zero_limit(self):
        self.assertEqual(screen.fit_left("abc", 0), "")

    def test_ascii_marker(self):
        self.assertTrue(screen.fit_left("a" * 50, 10).isascii())


class TestPadAndRjust(unittest.TestCase):
    def test_pad_reaches_exact_width(self):
        for text in ("abc", "한글 파일", "", "매우매우매우긴한글이름입니다"):
            self.assertEqual(screen.width(screen.pad(text, 20)), 20, text)

    def test_rjust_reaches_exact_width(self):
        for text in ("1.2 MB", "한글", ""):
            self.assertEqual(screen.width(screen.rjust(text, 12)), 12, text)

    def test_rjust_aligns_right(self):
        self.assertTrue(screen.rjust("9 B", 8).endswith("9 B"))
        self.assertTrue(screen.rjust("9 B", 8).startswith(" "))


class FakeOut:
    def __init__(self):
        self.chunks = []

    def write(self, text):
        self.chunks.append(text)

    def flush(self):
        pass

    @property
    def text(self):
        return "".join(self.chunks)


class TestRenderer(unittest.TestCase):
    def setUp(self):
        self.out = FakeOut()
        self._real = term.sys.stdout
        term.sys.stdout = self.out
        self.addCleanup(lambda: setattr(term.sys, "stdout", self._real))

    def test_first_render_writes_every_line(self):
        renderer = screen.Renderer()
        renderer.render(["one", "two", "three"], (20, 3))
        self.assertIn("one", self.out.text)
        self.assertIn("two", self.out.text)
        self.assertIn("three", self.out.text)

    def test_unchanged_lines_are_not_rewritten(self):
        renderer = screen.Renderer()
        renderer.render(["one", "two"], (20, 2))
        self.out.chunks.clear()

        renderer.render(["one", "CHANGED"], (20, 2))
        self.assertIn("CHANGED", self.out.text)
        self.assertNotIn("one", self.out.text)

    def test_identical_frame_writes_nothing(self):
        renderer = screen.Renderer()
        renderer.render(["a", "b"], (20, 2))
        self.out.chunks.clear()

        renderer.render(["a", "b"], (20, 2))
        self.assertEqual(self.out.text, "")

    def test_resize_forces_full_redraw(self):
        renderer = screen.Renderer()
        renderer.render(["a", "b"], (20, 2))
        self.out.chunks.clear()

        renderer.render(["a", "b"], (40, 2))
        self.assertIn(term.CLEAR, self.out.text)
        self.assertIn("a", self.out.text)

    def test_missing_lines_are_blanked(self):
        renderer = screen.Renderer()
        renderer.render(["a", "b", "c"], (20, 3))
        self.out.chunks.clear()

        renderer.render(["a"], (20, 3))  # 2·3번째 줄이 사라졌다
        self.assertIn("\x1b[K", self.out.text)


if __name__ == "__main__":
    unittest.main()


class TestWindowSize(unittest.TestCase):
    """켤 때 창을 키운다. 다만 남의 창을 좁히거나 바꿔놓고 나가지 않는다."""

    def test_the_sequence_puts_rows_before_columns(self):
        """xterm 의 CSI 8 은 행이 먼저다. 뒤집으면 창이 엉뚱하게 잡힌다."""
        self.assertEqual(term.resize_seq(160, 48), "\x1b[8;48;160t")

    def test_a_small_window_grows(self):
        self.assertEqual(term.grown_to((80, 24), (160, 48)), (160, 48))

    def test_a_wide_window_is_left_alone(self):
        """넓게 쓰던 사람의 창을 좁히는 것은 도움이 아니다."""
        self.assertEqual(term.grown_to((200, 60), (160, 48)), (200, 60))

    def test_each_side_grows_on_its_own(self):
        self.assertEqual(term.grown_to((200, 24), (160, 48)), (200, 48))
        self.assertEqual(term.grown_to((80, 60), (160, 48)), (160, 60))

    def test_it_is_twice_the_usual_terminal(self):
        """80x24 가 터미널의 오랜 기본값이다. 그 두 배로 잡았다."""
        self.assertEqual((config.WINDOW_COLS, config.WINDOW_ROWS), (160, 48))

    def test_nothing_is_written_when_there_is_no_terminal(self):
        """파이프로 돌 때 시퀀스를 뱉으면 출력에 쓰레기가 섞인다."""
        written = []
        original = term.write
        term.write = written.append
        self.addCleanup(lambda: setattr(term, "write", original))

        term.resize(160, 48)  # 테스트 실행 중 stdout 은 tty 가 아니다
        self.assertEqual(written, [])

    def test_without_a_wanted_size_the_window_is_left_alone(self):
        self.assertIsNone(term.Terminal()._want)
        self.assertIsNone(term.Terminal()._size_before)
