from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from src.fetchers import rss


def test_fetch_parses_rss_jobs_and_uses_configured_company(monkeypatch):
    response = Mock()
    feed = b"""<?xml version="1.0"?>
    <rss><channel><item>
      <guid>role-1</guid>
      <title>Graduate Software Engineer</title>
      <link>https://careers.example/jobs/1?utm_source=feed</link>
      <description><![CDATA[<p>Python role</p><p>Location: Bengaluru</p>]]></description>
      <pubDate>Tue, 13 Oct 2026 10:00:00 +0000</pubDate>
    </item></channel></rss>"""
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [feed]
    monkeypatch.setattr(rss.requests, "get", Mock(return_value=response))

    jobs = rss.fetch({
        "name": "Example Jobs",
        "company": "Example Co",
        "url": "https://careers.example/feed.xml",
        "domains": ["careers.example"],
    })

    assert len(jobs) == 1
    assert jobs[0].company == "Example Co"
    assert jobs[0].title == "Graduate Software Engineer"
    assert jobs[0].location == "Bengaluru"
    assert jobs[0].source == "rss"
    assert jobs[0].external_id == "role-1"
    assert jobs[0].posted_at == datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    assert "Python role" in jobs[0].description


def test_fetch_parses_atom_link_and_entry_metadata(monkeypatch):
    response = Mock()
    feed = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>tag:example.com,2026:2</id>
        <title>Data Analyst Intern</title>
        <link rel="alternate" href="https://jobs.example/2"/>
        <summary>Remote internship</summary>
        <updated>2026-10-12T12:30:00Z</updated>
      </entry>
    </feed>"""
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [feed]
    monkeypatch.setattr(rss.requests, "get", Mock(return_value=response))

    jobs = rss.fetch({
        "name": "Example Feed",
        "url": "https://example.com/feed.atom",
        "domains": ["jobs.example"],
    })

    assert len(jobs) == 1
    assert jobs[0].company == "Example Feed"
    assert jobs[0].apply_url == "https://jobs.example/2"
    assert jobs[0].remote is True


def test_fetch_splits_configured_company_prefix_and_parses_region(monkeypatch):
    response = Mock()
    feed = b"""<?xml version="1.0"?>
    <rss><channel><item>
      <title>Example Labs: Junior Software Engineer</title>
      <region>Remote - Americas</region>
      <link>https://weworkremotely.com/remote-jobs/example-labs-junior-engineer</link>
      <description>Build software with our engineering team.</description>
    </item></channel></rss>"""
    response.raise_for_status.return_value = None
    response.iter_content.return_value = [feed]
    monkeypatch.setattr(rss.requests, "get", Mock(return_value=response))

    jobs = rss.fetch({
        "name": "We Work Remotely - Programming",
        "url": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "domains": ["weworkremotely.com"],
        "company_in_title": True,
    })

    assert len(jobs) == 1
    assert jobs[0].company == "Example Labs"
    assert jobs[0].title == "Junior Software Engineer"
    assert jobs[0].location == "Remote - Americas"
    assert jobs[0].remote is True


@pytest.mark.parametrize(
    "source",
    [
        {"name": "Feed", "url": "http://example.com/jobs.xml"},
        {"name": "Feed", "url": "https://example.com/jobs.xml", "domains": []},
        {"name": "", "url": "https://example.com/jobs.xml", "domains": ["example.com"]},
    ],
)
def test_fetch_rejects_invalid_source_configuration(source):
    with pytest.raises(ValueError):
        rss.fetch(source)
