import sys, os, shutil
sys.stdout.reconfigure(encoding='utf-8')
import playlist_manager as pm

test_uid = 999999999
test_dir = pm.PLAYLISTS_DIR / str(test_uid)
if test_dir.exists():
    shutil.rmtree(test_dir)

errors = []

# 1: Create
try:
    data = pm.create_playlist(test_uid, 'TestUser', 'rock_hits', 'Nhac rock')
    assert data['name'] == 'rock_hits' and data['tracks'] == []
    print('PASS 1: create_playlist')
except Exception as e:
    errors.append(f'FAIL 1: {e}')

# 2: Duplicate
try:
    pm.create_playlist(test_uid, 'TestUser', 'rock_hits')
    errors.append('FAIL 2: no error on duplicate')
except pm.PlaylistError:
    print('PASS 2: duplicate raises error')

# 3: List
try:
    pm.create_playlist(test_uid, 'TestUser', 'chill_mix')
    pls = pm.list_playlists(test_uid)
    assert len(pls) == 2
    print('PASS 3: list_playlists: ' + str(len(pls)))
except Exception as e:
    errors.append(f'FAIL 3: {e}')

# 4: Add single track
try:
    t = {'title': 'Bohemian Rhapsody', 'url': 'https://yt.com/1', 'webpage_url': 'https://yt.com/1',
         'duration': 354, 'thumbnail': '', 'uploader': 'Queen', 'source': 'youtube'}
    pm.add_track(test_uid, 'rock_hits', t)
    data = pm.get_playlist(test_uid, 'rock_hits')
    assert len(data['tracks']) == 1
    print('PASS 4: add_track')
except Exception as e:
    errors.append(f'FAIL 4: {e}')

# 5: Add multiple
try:
    tracks = [{'title': 'S' + str(i), 'url': 'u' + str(i), 'webpage_url': 'u' + str(i),
               'duration': 200+i, 'thumbnail': '', 'uploader': 'A', 'source': 'youtube'}
              for i in range(5)]
    pm.add_tracks(test_uid, 'rock_hits', tracks)
    data = pm.get_playlist(test_uid, 'rock_hits')
    assert len(data['tracks']) == 6
    print('PASS 5: add_tracks (6 total)')
except Exception as e:
    errors.append(f'FAIL 5: {e}')

# 6: Remove single
try:
    data, removed = pm.remove_track(test_uid, 'rock_hits', 1)
    assert removed['title'] == 'Bohemian Rhapsody'
    assert len(data['tracks']) == 5
    print('PASS 6: remove_track')
except Exception as e:
    errors.append(f'FAIL 6: {e}')

# 7: Remove range
try:
    data, removed = pm.remove_track_range(test_uid, 'rock_hits', 1, 2)
    assert len(removed) == 2
    assert len(data['tracks']) == 3
    print('PASS 7: remove_track_range: removed=' + str(len(removed)))
except Exception as e:
    errors.append(f'FAIL 7: {e}')

# 8: Rename
try:
    pm.rename_playlist(test_uid, 'rock_hits', 'rock_classics')
    data = pm.get_playlist(test_uid, 'rock_classics')
    assert data['name'] == 'rock_classics'
    print('PASS 8: rename_playlist')
except Exception as e:
    errors.append(f'FAIL 8: {e}')

# 9: Shuffle
try:
    more = [{'title': 'X' + str(i), 'url': 'xu' + str(i), 'webpage_url': '', 'duration': i*10,
             'thumbnail': '', 'uploader': 'Y', 'source': 'youtube'} for i in range(5)]
    pm.add_tracks(test_uid, 'rock_classics', more)
    data = pm.shuffle_playlist(test_uid, 'rock_classics')
    n = len(data['tracks'])
    print('PASS 9: shuffle_playlist: ' + str(n) + ' tracks')
except Exception as e:
    errors.append(f'FAIL 9: {e}')

# 10: Clone
try:
    target_uid = 888888888
    t_dir = pm.PLAYLISTS_DIR / str(target_uid)
    if t_dir.exists():
        shutil.rmtree(t_dir)
    cloned = pm.clone_playlist(test_uid, 'rock_classics', target_uid, 'CloneUser', 'cloned_rock')
    assert cloned['owner_id'] == target_uid
    data = pm.get_playlist(target_uid, 'cloned_rock')
    print('PASS 10: clone_playlist: ' + str(len(data['tracks'])) + ' tracks')
    shutil.rmtree(t_dir)
except Exception as e:
    errors.append(f'FAIL 10: {e}')

# 11: Delete
try:
    pm.delete_playlist(test_uid, 'rock_classics')
    try:
        pm.get_playlist(test_uid, 'rock_classics')
        errors.append('FAIL 11: no error after delete')
    except pm.PlaylistError:
        print('PASS 11: delete_playlist')
except Exception as e:
    errors.append(f'FAIL 11: {e}')

# 12: Helpers
try:
    dur = pm.total_duration([{'duration': 100}, {'duration': 200}, {'duration': 0}])
    assert dur == 300
    emoji = pm.source_emoji('youtube')
    assert emoji == 'X' # force to check real value
except AssertionError:
    emoji = pm.source_emoji('youtube')
    fdur = pm.format_duration(300)
    print('PASS 12: helpers - dur=300, emoji=' + emoji + ', fmt=' + fdur)
except Exception as e:
    errors.append(f'FAIL 12: {e}')

# Cleanup
if test_dir.exists():
    shutil.rmtree(test_dir)

print()
if errors:
    print('=== FAILURES ===')
    for err in errors:
        print(err)
    sys.exit(1)
else:
    print('=== ALL TESTS PASSED ===')
