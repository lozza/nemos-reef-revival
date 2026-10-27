"""Plays one player's reef on another device.

A device that registered its own player (for example "lozzareeves2" on an emulator) is linked to an
existing player ("lozzareeves"): from its next start it loads that player's reef, and the spare player made
on it is deleted with its reef and friends.

    python server/link_device.py lozzareeves2 lozzareeves
    docker exec <container> python server/link_device.py lozzareeves2 lozzareeves

Run it on the computer or container that runs the server; --data names the save folder if it is not the
usual one. Without arguments it lists the players.
"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('new_player', nargs='?', help='the player the other device made (its reef is deleted)')
    ap.add_argument('existing_player', nargs='?', help='the player whose reef that device should play')
    ap.add_argument('--data', help='save folder (default: /data in Docker, else server/data)')
    args = ap.parse_args()
    data = args.data or ('/data' if os.path.exists('/data/nemo.db') else os.path.join(HERE, 'data'))
    db_path = os.path.join(data, 'nemo.db')
    if not os.path.exists(db_path):
        sys.exit('No saves at %s; pass --data with the folder that holds nemo.db.' % db_path)
    db = sqlite3.connect(db_path)
    db.execute('CREATE TABLE IF NOT EXISTS devices (udid TEXT PRIMARY KEY, uid TEXT)')
    users = db.execute('SELECT uid, udid, name FROM users ORDER BY lower(name)').fetchall()
    if not args.existing_player:
        print('Players:')
        for uid, _, name in users:
            extra = db.execute('SELECT count(*) FROM devices WHERE uid=?', (uid,)).fetchone()[0]
            print('   %-24s%s' % (name, ' (+%d linked device%s)' % (extra, 's' * (extra > 1)) if extra else ''))
        print('\nUsage: python server/link_device.py <new player> <existing player>')
        return
    by_name = {name.lower(): (uid, udid, name) for uid, udid, name in users}
    new, old = by_name.get(args.new_player.lower()), by_name.get(args.existing_player.lower())
    for wanted, found in ((args.new_player, new), (args.existing_player, old)):
        if not found:
            sys.exit('No player called "%s". Run without arguments to list the players.' % wanted)
    if new[0] == old[0]:
        sys.exit('Those are the same player.')
    devices = [new[1]] + [r[0] for r in db.execute('SELECT udid FROM devices WHERE uid=?', (new[0],))]
    with db:
        for udid in devices:
            db.execute('INSERT OR REPLACE INTO devices VALUES (?,?)', (udid, old[0]))
        db.execute('DELETE FROM users WHERE uid=?', (new[0],))
        db.execute('DELETE FROM worlds WHERE uid=?', (new[0],))
        db.execute('DELETE FROM friends WHERE uid=? OR friend=?', (new[0], new[0]))
    print('Linked: the device that made "%s" now plays "%s". "%s" and its reef are deleted.'
          % (new[2], old[2], new[2]))
    print('Close the game on that device and open it again.')


if __name__ == '__main__':
    main()
