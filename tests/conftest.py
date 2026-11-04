import pytest

from wikichat.config import Settings
from wikichat.crawler import Page, save_pages

PAGES = [
    Page(1, "GPU Cluster", 0, 11, "2026-10-01T10:00:00Z", "https://wiki.example/GPU_Cluster",
         "The group runs a '''GPU cluster''' called [[Noctua]].\n\n== Access ==\n"
         "To get access, write an email to the [[Admins|admin team]] and ask for an account.\n"
         "Then log in with SSH on port 22.\n\n== Rules ==\nDo not run jobs longer than 48 hours.\n"
         "[[Kategorie:Infrastruktur]]"),
    Page(2, "Dienstreise", 0, 22, "2026-09-12T08:00:00Z", "https://wiki.example/Dienstreise",
         "{{Infobox|Kontakt=Sekretariat|Formular=Reiseantrag}}\n"
         "Eine Dienstreise muss vor der Reise beim Sekretariat beantragt werden.\n\n"
         "== Abrechnung ==\nDie Reisekosten werden nach der Reise über das Formular abgerechnet."),
    Page(3, "Onboarding", 0, 33, "2026-08-01T08:00:00Z", "https://wiki.example/Onboarding",
         "New members should get a key, a laptop and a wiki account in the first week.\n"
         "<!-- hidden note -->\n== Paper templates ==\nLaTeX templates are in the group GitLab.\n\n"
         "== Wi-Fi ==\nThe guest network is DICE-Guest. Password: Tr0ub4dor&3xyz"),
    Page(4, "Admin secrets", 0, 44, "2026-08-01T08:00:00Z", "https://wiki.example/Admin_secrets",
         "Root login for the backup server and other private notes.\n[[Category:Vertraulich]]", ["Vertraulich"]),
]


@pytest.fixture
def settings(tmp_path, monkeypatch):
    from wikichat import vault

    monkeypatch.setattr(vault, "get", lambda name: "")  # never touch the real keychain in tests
    s = Settings(_env_file=None, data_dir=tmp_path, llm_provider="none", top_k=4,
                 exclude_categories="Vertraulich", rate_limit_per_minute=0)
    save_pages(s.pages_file, PAGES)
    return s
