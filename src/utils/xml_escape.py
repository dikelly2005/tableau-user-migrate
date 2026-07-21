# XML attribute escaping utility for safe payload construction
# Co-authored with CoCo
from xml.sax.saxutils import escape, quoteattr


def xml_attr(value) -> str:
    """Escape a value for use inside an XML attribute (with surrounding quotes)."""
    return quoteattr(str(value)) if value is not None else '""'


def xml_attr_val(value) -> str:
    """Escape a value for use as an XML attribute value WITHOUT surrounding quotes.
    Use inside f-string patterns like: name="{xml_attr_val(name)}"
    """
    s = str(value) if value is not None else ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def xml_text(value) -> str:
    """Escape a value for use as XML text content."""
    return escape(str(value)) if value is not None else ""
