from unittest.mock import Mock

from src.fetchers import smartrecruiters


def test_smartrecruiters_fetch_normalizes_public_postings(monkeypatch):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "totalFound": 1,
        "content": [
            {
                "id": "123",
                "name": "Graduate Software Engineer",
                "releasedDate": "2026-10-12T10:00:00Z",
                "location": {
                    "city": "Bengaluru",
                    "region": "Karnataka",
                    "country": "India",
                    "remote": False,
                },
                "department": {"label": "Engineering"},
                "function": {"label": "Software Development"},
            }
        ],
    }
    get = Mock(return_value=response)
    monkeypatch.setattr(smartrecruiters.requests, "get", get)

    jobs = smartrecruiters.fetch("Example Co", "example")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.company == "Example Co"
    assert job.title == "Graduate Software Engineer"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.source == "smartrecruiters"
    assert job.external_id == "123"
    assert job.apply_url == "https://careers.smartrecruiters.com/example/123"
    assert job.posted_at.isoformat() == "2026-10-12T10:00:00+00:00"
    get.assert_called_once()
    assert get.call_args.kwargs["params"] == {"limit": 100, "offset": 0}


def test_smartrecruiters_fetch_paginates(monkeypatch):
    page1 = Mock(status_code=200)
    page1.json.return_value = {"totalFound": 101, "content": [{"id": str(i), "name": f"Role {i}"} for i in range(100)]}
    page2 = Mock(status_code=200)
    page2.json.return_value = {"totalFound": 101, "content": [{"id": "100", "name": "Role 100"}]}
    get = Mock(side_effect=[page1, page2])
    monkeypatch.setattr(smartrecruiters.requests, "get", get)

    jobs = smartrecruiters.fetch("Example Co", "example")

    assert len(jobs) == 101
    assert [call.kwargs["params"]["offset"] for call in get.call_args_list] == [0, 100]
