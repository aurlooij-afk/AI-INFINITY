"""No-network guard for the eight real source clips in the One Idea campaign."""
from studio_ultimate import _ONE_IDEA_PEXELS_MOTION_SOURCES


def test_eight_story_scenes_have_distinct_attributed_free_source_clips():
    rows = _ONE_IDEA_PEXELS_MOTION_SOURCES
    assert set(rows) == set(range(1, 9)), sorted(rows)
    ids = [row["source_id"] for row in rows.values()]
    urls = [row["source_url"] for row in rows.values()]
    media = [row["media_url"] for row in rows.values()]
    creators = [row["creator"] for row in rows.values()]
    assert len(set(ids)) == 8, ids
    assert len(set(urls)) == 8, urls
    assert len(set(media)) == 8, media
    assert all(url.startswith("https://www.pexels.com/video/") for url in urls), urls
    assert all(url.startswith("https://videos.pexels.com/video-files/") for url in media), media
    assert all(name.strip() for name in creators), creators
    assert all(str(row["source_id"]) in row["media_url"] for row in rows.values())
    assert all(row["title"] and row["description"] and row["visual_match"] for row in rows.values())
    # Scene 6 uses a confirmed 1080p30 Pexels download endpoint as the
    # smaller, source-attributed backup for the inaccessible UHD edit-timeline clip.
    scene6 = rows[6]
    assert scene6["source_id"] == 5584722, scene6
    assert scene6["source_url"] == "https://www.pexels.com/video/person-watching-and-editing-video-5584722/"
    assert scene6["media_url"] == "https://videos.pexels.com/video-files/5584722/5584722-hd_1920_1080_30fps.mp4"
    assert scene6["creator"] == "Nino Souza"
    assert "editing" in scene6["title"].lower() and "video" in scene6["title"].lower()


if __name__ == "__main__":
    test_eight_story_scenes_have_distinct_attributed_free_source_clips()
    print("PASS: eight unique, item-attributed free-source clips are mapped to the eight story beats.")
