import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EvernoteResource(object):
    data_bin: bytes
    size: int
    md5: str
    mime: str
    file_name: str
    source_url: str = ""  # Source URL from resource-attributes


@dataclass
class EvernoteNote(object):
    title: str
    created: datetime
    updated: datetime
    content: str  # noqa: WPS110
    tags: list[str]
    author: str
    url: str
    is_webclip: bool
    resources: list[EvernoteResource]
    _note_hash: str = None

    def resource_by_md5(self, md5):
        for resource in self.resources:
            if resource.md5 == md5:
                return resource
        return None

    @property
    def note_hash(self):
        if self._note_hash is None:
            hashable = [
                self.title,
                self.created.isoformat(),
                self.updated.isoformat(),
                self.content,
                "".join(self.tags),
                self.author,
                self.url,
            ]

            s1_hash = hashlib.sha1()
            for h in hashable:
                s1_hash.update(h.encode("utf-8"))
            self._note_hash = s1_hash.hexdigest()  # noqa: WPS601

        return self._note_hash


@dataclass
class NoteParseResult:
    """Result of parsing a single note from ENEX."""

    note: EvernoteNote | None
    raw_xml: str  # Original XML element as string for failed export
    error: Exception | None
    parse_success: bool
    skip_reason: str | None = None  # Reason for skipping (if applicable)

    @property
    def failed(self) -> bool:
        return not self.parse_success or self.note is None


@dataclass
class ParseStats:
    """Statistics from parsing an ENEX file."""

    total: int = 0
    successful: int = 0
    failed: int = 0
    results: list[NoteParseResult] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return (self.successful / self.total) * 100
