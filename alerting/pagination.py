"""Chia tin nhắn Telegram dài thành các trang HTML hợp lệ."""
from html import escape
from html.parser import HTMLParser


TELEGRAM_MAX_TEXT_LENGTH = 4000
_PAGE_HEADER_RESERVE = 20
_VOID_TAGS = {"br", "hr", "img"}


def telegram_text_length(text: str) -> int:
    """Đếm độ dài sau khi bỏ HTML theo đơn vị UTF-16 mà Telegram sử dụng."""
    parser = _VisibleTextParser()
    parser.feed(text)
    return len(parser.text.encode("utf-16-le")) // 2


class _VisibleTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = ""

    def handle_data(self, data):
        self.text += data


class _PageParser(HTMLParser):
    def __init__(self, max_length):
        super().__init__(convert_charrefs=True)
        self.limit = max_length - _PAGE_HEADER_RESERVE
        self.pages = []
        self.current = []
        self.visible_units = 0
        self.open_tags = []

    def _open_markup(self):
        return "".join(raw for _, raw in self.open_tags)

    def _close_markup(self):
        return "".join(f"</{tag}>" for tag, _ in reversed(self.open_tags))

    def _flush(self):
        self.pages.append("".join(self.current) + self._close_markup())
        self.current = [self._open_markup()]
        self.visible_units = 0

    @staticmethod
    def _prefix_length(text, max_units):
        used = 0
        for index, char in enumerate(text):
            units = 2 if ord(char) > 0xFFFF else 1
            if used + units > max_units:
                return index
            used += units
        return len(text)

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        self.current.append(raw)
        if tag not in _VOID_TAGS:
            self.open_tags.append((tag, raw))

    def handle_startendtag(self, tag, attrs):
        self.current.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        self.current.append(f"</{tag}>")
        for index in range(len(self.open_tags) - 1, -1, -1):
            if self.open_tags[index][0] == tag:
                del self.open_tags[index:]
                break

    def handle_data(self, data):
        remaining = data
        while remaining:
            capacity = self.limit - self.visible_units
            take = self._prefix_length(remaining, capacity)
            if take == 0:
                self._flush()
                continue

            if take < len(remaining):
                split_at = max(remaining.rfind("\n", 0, take + 1), remaining.rfind(" ", 0, take + 1))
                if split_at >= take // 2:
                    take = split_at + 1

            piece = remaining[:take]
            self.current.append(escape(piece, quote=False))
            self.visible_units += len(piece.encode("utf-16-le")) // 2
            remaining = remaining[take:]
            if remaining:
                self._flush()


def paginate_message(text: str, max_length: int = TELEGRAM_MAX_TEXT_LENGTH) -> list[str]:
    """Giữ nguyên tin ngắn; tin dài được chia trang, cân bằng lại tag HTML."""
    if max_length <= _PAGE_HEADER_RESERVE:
        raise ValueError("max_length quá nhỏ để thêm chỉ số trang")
    if telegram_text_length(text) <= max_length:
        return [text]

    parser = _PageParser(max_length)
    parser.feed(text)
    if parser.current and parser.visible_units:
        parser.pages.append("".join(parser.current) + parser._close_markup())

    page_count = len(parser.pages)
    return [f"<b>[{index}/{page_count}]</b>\n{page}" for index, page in enumerate(parser.pages, 1)]