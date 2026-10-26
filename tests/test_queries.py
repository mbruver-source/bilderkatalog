from bilderkatalog import albums, queries


def add(conn, pid, taken, city=None, cc=None, path="/A", source="exif", video=0, name=None):
    conn.execute(
        """INSERT INTO photo (id, name, parent_path, is_video, taken_at, date_source, year, month,
                              city, country_code, lat, geocoded)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
        (pid, name or f"{pid}.jpg", path, video, taken, source,
         int(taken[:4]) if taken else None, int(taken[5:7]) if taken else None,
         city, cc, 1.0 if city else None),
    )


def seed(conn):
    add(conn, "a", "2020-06-01T10:00:00", "Paris", "FR", "/Reisen/Paris")
    add(conn, "b", "2020-06-01T11:00:00", "Paris", "FR", "/Reisen/Paris")
    add(conn, "c", "2021-01-02T09:00:00", "Berlin", "DE", "/Reisen_2")
    add(conn, "d", None, path="/Sonstiges", source=None, name="100%_foto.jpg")
    add(conn, "e", "2019-03-12T00:00:00", path="/WhatsApp", source="filename", video=1)
    conn.commit()


def ids(result):
    return [i["id"] for i in result["items"]]


def test_filters(conn):
    seed(conn)
    run = lambda **kw: ids(queries.list_photos(conn, queries.Filter(**kw)))  # noqa: E731
    assert run(year=2020) == ["b", "a"]
    assert run(year=2020, month=6, country="FR", city="Paris") == ["b", "a"]
    assert run(folder="/Reisen") == ["b", "a"]  # "/Reisen_2" darf nicht passen
    assert run(media="video") == ["e"]
    assert set(run(no_date=True)) == {"d", "e"}
    assert set(run(no_gps=True)) == {"d", "e"}
    assert run(q="100%") == ["d"]
    assert run(date_from="2020-06-01", date_to="2020-06-01") == ["b", "a"]
    assert run(date_from="2020-06-01T10:30:00", date_to="2020-06-01T11:00:00") == ["b"]


def test_sorting_and_paging(conn):
    seed(conn)
    flt = queries.Filter()
    assert ids(queries.list_photos(conn, flt, "date_asc")) == ["e", "a", "b", "c", "d"]
    page = queries.list_photos(conn, flt, "date_desc", limit=2, offset=1)
    assert page["total"] == 5 and ids(page) == ["b", "a"]


def test_deleted_photos_hidden(conn):
    seed(conn)
    conn.execute("UPDATE photo SET deleted = 1 WHERE id = 'a'")
    assert "a" not in ids(queries.list_photos(conn, queries.Filter()))
    assert queries.facets(conn)["total"] == 4


def test_facets(conn):
    seed(conn)
    f = queries.facets(conn)
    assert {(y["year"], y["month"], y["n"]) for y in f["years"]} == {(2021, 1, 1), (2020, 6, 2), (2019, 3, 1)}
    assert {(p["country_code"], p["city"], p["n"]) for p in f["places"]} == {("FR", "Paris", 2), ("DE", "Berlin", 1)}
    assert f["special"] == {"no_date": 2, "no_gps": 2, "videos": 1, "partial": 0, "favorites": 0,
                            "date_manual": 0, "dup_files": 0, "dup_copies": 0, "dup_groups": 0,
                            "place_manual": 0}


def test_album_filter_and_crud(conn):
    seed(conn)
    album = albums.create_album(conn, "Paris 2020")
    assert albums.add_photos(conn, album["id"], ["a", "b", "a", "gibt-es-nicht"]) == 2
    assert ids(queries.list_photos(conn, queries.Filter(album=album["id"]))) == ["b", "a"]
    assert albums.remove_photos(conn, album["id"], ["a"]) == 1
    listed = albums.list_albums(conn)
    assert listed[0]["n"] == 1 and listed[0]["cover"] == "b"
    albums.delete_album(conn, album["id"])
    assert conn.execute("SELECT COUNT(*) FROM photo").fetchone()[0] == 5  # Bilder bleiben


def test_events(conn):
    for i in range(6):
        add(conn, f"p{i}", f"2022-08-01T1{i}:00:00", "Rom", "IT")
    for i in range(5):
        add(conn, f"q{i}", f"2022-08-03T1{i}:00:00", "Neapel", "IT")
    add(conn, "lonely", "2022-09-01T10:00:00", "Rom", "IT")
    conn.commit()
    events = queries.events(conn, gap_hours=6, min_size=5)
    assert [(e["city"], e["count"]) for e in events] == [("Neapel", 5), ("Rom", 6)]
    assert events[1]["start"] == "2022-08-01T10:00:00"
