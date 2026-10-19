"""Deterministic research fixtures for the mock tools and the deterministic research agent.

SYNTHETIC: every place, fact, and document here is fictional (the island of Veloria). All
fixtures share one corpus so that search ranking is exercised, not bypassed.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agents.interface import AgentEnvironment, AgentTask, EvidenceStatus
from app.domain.enums import AgentOutcome
from app.runtime.clock import ManualClock
from app.tools.base import ToolErrorCode
from app.tools.documents import Document, DocumentCorpus
from app.tools.fetch import MockFetchTool
from app.tools.mock import FailOutcome, MockScript, RespondOutcome, TimeoutOutcome
from app.tools.search import MockSearchTool

FIXTURE_START = datetime(2026, 1, 1, tzinfo=UTC)

CORPUS = DocumentCorpus(
    documents=[
        Document(
            id="veloria-almanac",
            title="Veloria Almanac (synthetic)",
            content=(
                "Port Annick is the largest harbour town on the island of Veloria. "
                "The Port Annick lighthouse was completed in 1873 after five years of work."
            ),
        ),
        Document(
            id="port-annick-harbour-guide",
            title="Port Annick Harbour Guide (synthetic)",
            content=(
                "Visitors can climb the Port Annick lighthouse, which was completed in 1873. "
                "Opening hours vary by season."
            ),
        ),
        Document(
            id="veloria-fisheries-report",
            title="Veloria Fisheries Report (synthetic)",
            content="The Port Annick fleet landed 3,200 tonnes of kelp in the last reporting year.",
        ),
        Document(
            id="port-annick-census",
            title="Port Annick Census Summary (synthetic)",
            content=(
                "Recorded population of Port Annick: 4,100 in 1900 and 9,800 in 1950. "
                "No earlier census exists."
            ),
        ),
        Document(
            id="university-of-veloria-prospectus",
            title="University of Veloria Prospectus (synthetic)",
            content="The University of Veloria was founded in 1902 and teaches marine science.",
        ),
        Document(
            id="university-charter-history",
            title="A History of the University Charter (synthetic)",
            content=(
                "Teaching began earlier, but according to its charter the University of Veloria "
                "was founded in 1907."
            ),
        ),
    ]
)

_TOOLS = ["search_documents", "fetch_document"]

LIGHTHOUSE_TASK = AgentTask(
    prompt="In what year was the Port Annick lighthouse completed? Cite your sources.",
    available_tools=_TOOLS,
    variables={
        "search_query": "Port Annick lighthouse completed",
        "answer_pattern": r"completed in (\d{4})",
        "subject": "the completion year of the Port Annick lighthouse",
    },
)

UNIVERSITY_TASK = AgentTask(
    prompt="When was the University of Veloria founded? Cite your sources.",
    available_tools=_TOOLS,
    variables={
        "search_query": "University of Veloria founded",
        "answer_pattern": r"founded in (\d{4})",
        "subject": "the founding year of the University of Veloria",
    },
)

POPULATION_TASK = AgentTask(
    prompt="What was the population of Port Annick in 1850? Cite your sources.",
    available_tools=_TOOLS,
    variables={
        "search_query": "Port Annick population census",
        "answer_pattern": r"([\d,]+) in 1850",
        "subject": "the population of Port Annick in 1850",
    },
)


@dataclass(frozen=True)
class ResearchFixture:
    name: str
    description: str
    task: AgentTask
    expected_outcome: AgentOutcome
    expected_evidence_status: EvidenceStatus
    search_script: MockScript = field(default_factory=MockScript)
    fetch_script: MockScript = field(default_factory=MockScript)
    corpus: DocumentCorpus = CORPUS

    def build(
        self, run_id: str, *, call_budget: int | None = None
    ) -> tuple[AgentEnvironment, MockSearchTool, MockFetchTool]:
        """Fresh clock, tools, and environment, so every execution starts from the same state."""
        clock = ManualClock(FIXTURE_START)
        search = MockSearchTool(self.corpus, clock, self.search_script)
        fetch = MockFetchTool(self.corpus, clock, self.fetch_script)
        environment = AgentEnvironment.create(
            run_id, [search, fetch], clock, call_budget=call_budget
        )
        return environment, search, fetch


FIXTURES: dict[str, ResearchFixture] = {
    fixture.name: fixture
    for fixture in [
        ResearchFixture(
            name="successful-research",
            description="Two sources agree on the answer; a third is consulted but irrelevant.",
            task=LIGHTHOUSE_TASK,
            expected_outcome=AgentOutcome.ANSWERED,
            expected_evidence_status=EvidenceStatus.CONSISTENT,
        ),
        ResearchFixture(
            name="conflicting-evidence",
            description="Two sources give different founding years.",
            task=UNIVERSITY_TASK,
            expected_outcome=AgentOutcome.INSUFFICIENT_INFORMATION,
            expected_evidence_status=EvidenceStatus.CONFLICTING,
        ),
        ResearchFixture(
            name="insufficient-evidence",
            description="Relevant documents exist but none states the requested fact.",
            task=POPULATION_TASK,
            expected_outcome=AgentOutcome.INSUFFICIENT_INFORMATION,
            expected_evidence_status=EvidenceStatus.INSUFFICIENT,
        ),
        ResearchFixture(
            name="missing-document",
            description=(
                "Search (predefined response) returns a document that no longer exists; the agent "
                "answers from the remaining source and reports the missing one."
            ),
            task=LIGHTHOUSE_TASK,
            expected_outcome=AgentOutcome.ANSWERED,
            expected_evidence_status=EvidenceStatus.CONSISTENT,
            search_script=MockScript(
                default=RespondOutcome(
                    payload={
                        "results": [
                            {
                                "document_id": "port-annick-archive",
                                "title": "Port Annick Archive (synthetic, deleted)",
                                "score": 1.0,
                                "snippet": "Records of harbour construction.",
                            },
                            {
                                "document_id": "veloria-almanac",
                                "title": "Veloria Almanac (synthetic)",
                                "score": 0.75,
                                "snippet": "Port Annick is the largest harbour town.",
                            },
                        ]
                    }
                )
            ),
        ),
        ResearchFixture(
            name="malformed-response",
            description="The first search response violates the output schema; a retry succeeds.",
            task=LIGHTHOUSE_TASK,
            expected_outcome=AgentOutcome.ANSWERED,
            expected_evidence_status=EvidenceStatus.CONSISTENT,
            search_script=MockScript(
                by_call={1: RespondOutcome(payload={"hits": "<html>502 Bad Gateway</html>"})}
            ),
        ),
        ResearchFixture(
            name="tool-timeout",
            description="Every document fetch times out, even on retry; no evidence is reachable.",
            task=LIGHTHOUSE_TASK,
            expected_outcome=AgentOutcome.TASK_FAILED,
            expected_evidence_status=EvidenceStatus.UNAVAILABLE,
            fetch_script=MockScript(default=TimeoutOutcome(after_ms=30_000)),
        ),
        ResearchFixture(
            name="search-unavailable",
            description="Search keeps returning 'unavailable'; the agent gives up after one retry.",
            task=LIGHTHOUSE_TASK,
            expected_outcome=AgentOutcome.TASK_FAILED,
            expected_evidence_status=EvidenceStatus.UNAVAILABLE,
            search_script=MockScript(
                default=FailOutcome(
                    code=ToolErrorCode.UNAVAILABLE, message="503 Service Unavailable"
                )
            ),
        ),
    ]
}
