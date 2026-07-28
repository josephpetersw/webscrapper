"""Image download semantics: skip work already done, and never leave a
half-written file that the skip-check would later trust."""
import asyncio
import os

import pytest

from scraper.downloader import MAX_RETRIES, ImageDownloader

from conftest import FakeAsyncSession, FakeResponse


@pytest.fixture
def downloader(tmp_path):
    return ImageDownloader(base_dir=str(tmp_path / 'images'), concurrency=4)


@pytest.fixture
def save_dir(tmp_path):
    d = tmp_path / 'product' / 'images'
    d.mkdir(parents=True)
    return str(d)


def run(coro):
    return asyncio.run(coro)


def test_base_dir_is_created(tmp_path):
    ImageDownloader(base_dir=str(tmp_path / 'nested' / 'images'))
    assert os.path.isdir(tmp_path / 'nested' / 'images')


def test_downloads_and_names_the_file_from_the_url(downloader, save_dir):
    session = FakeAsyncSession([FakeResponse(200, content=b'\xff\xd8jpegbytes')])
    path = run(downloader.download_image(session, 'https://cdn.test/a/realme-5.jpg', save_dir))

    assert path == os.path.join(save_dir, 'realme-5.jpg')
    with open(path, 'rb') as f:
        assert f.read() == b'\xff\xd8jpegbytes'


def test_no_part_file_survives_a_successful_download(downloader, save_dir):
    session = FakeAsyncSession([FakeResponse(200, content=b'x')])
    run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir))
    assert os.listdir(save_dir) == ['a.jpg']


def test_existing_file_short_circuits_without_a_request(downloader, save_dir):
    existing = os.path.join(save_dir, 'a.jpg')
    with open(existing, 'wb') as f:
        f.write(b'cached')
    session = FakeAsyncSession([FakeResponse(200, content=b'fresh')])

    assert run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir)) == existing
    assert session.calls == []
    with open(existing, 'rb') as f:
        assert f.read() == b'cached'  # not overwritten


def test_permanent_failure_returns_none_and_writes_nothing(downloader, save_dir, no_sleep):
    session = FakeAsyncSession([FakeResponse(404)])
    assert run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir)) is None
    assert len(session.calls) == 1
    assert os.listdir(save_dir) == []


def test_transient_status_is_retried_then_succeeds(downloader, save_dir, no_sleep):
    session = FakeAsyncSession([FakeResponse(503), FakeResponse(200, content=b'ok')])
    path = run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir))
    assert path is not None
    assert len(session.calls) == 2


def test_transient_status_gives_up_after_max_retries(downloader, save_dir, no_sleep):
    session = FakeAsyncSession([FakeResponse(500)])
    assert run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir)) is None
    assert len(session.calls) == MAX_RETRIES + 1


def test_exception_is_retried_then_gives_up(downloader, save_dir, no_sleep):
    session = FakeAsyncSession([ConnectionError('reset')])
    assert run(downloader.download_image(session, 'https://cdn.test/a.jpg', save_dir)) is None
    assert len(session.calls) == MAX_RETRIES + 1
    assert os.listdir(save_dir) == []


def test_concurrency_semaphore_bounds_in_flight_downloads(tmp_path, save_dir):
    """The semaphore is the only thing keeping a 40-image product from opening
    40 sockets at once, so assert it actually gates."""
    dl = ImageDownloader(base_dir=str(tmp_path / 'images'), concurrency=2)
    in_flight = 0
    peak = 0

    class TrackingSession:
        async def get(self, url, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return FakeResponse(200, content=b'x')

    async def scenario():
        session = TrackingSession()
        await asyncio.gather(*[
            dl.download_image(session, f'https://cdn.test/{i}.jpg', save_dir)
            for i in range(8)
        ])

    run(scenario())
    assert peak <= 2
    assert len(os.listdir(save_dir)) == 8
