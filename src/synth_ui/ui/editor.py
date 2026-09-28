"""What the browser editor can do to the instrument's sets and rigs.

Every method here except the `*_controls` reads runs on the UI thread — the
server hands requests over through `SynthUI.run_on_ui` — so they change sets,
rigs and screens exactly as a touch would, with no locking.

## Two kinds of rig

- **The active rig** is being played, and while it is, the *engine* holds its
  truth: the effects screen edits the live chain and writes it back into the
  rig only on the way out. Edits here go the same way — live through the
  engine, so you hear them — and are saved immediately after.
- **Every other rig** is data. Editing one changes the store and nothing you
  can hear. There is deliberately no way to make a rig active from here: what
  the keyboard plays is chosen at the instrument.

Slow engine work (loading a plugin or an instrument) runs in the background, as
it does from the touchscreen, and hands its result back to the UI thread. Until
it has, the active rig's chain can't be edited — see `busy`.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from synth_ui.clients.controls import controls_for
from synth_ui.clients.eq import eq_layout_for
from synth_ui.clients.rig import Rig, RigEffect, new_id
from synth_ui.clients.set import SongSet
from synth_ui.server import EditorError
from synth_ui.ui.screens.effects import TRIM_RANGE_DB

if TYPE_CHECKING:
    from synth_ui.ui.app import SynthUI

logger = logging.getLogger(__name__)


def _not_found(what: str) -> EditorError:
    return EditorError(f"{what} not found", status=404)


class EditorController:
    def __init__(self, app: SynthUI):
        self._app = app
        # Set while a background engine operation on the active rig runs.
        self.busy = False
        self._controls_cache: dict[str, dict] = {}
        self._controls_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _active_rig(self) -> Rig | None:
        return self._app._rig_screen.active_rig

    def _is_active(self, rig: Rig) -> bool:
        active = self._active_rig()
        return active is not None and active.id == rig.id

    def snapshot(self, rig: Rig) -> dict:
        """A rig as the browser sees it — for the active rig, what's live.

        The active rig's stored copy lags the engine until the effects screen
        is left, so its chain, instrument patch, level and velocity come from
        wherever they currently are.
        """
        app = self._app
        data = rig.to_dict()
        voice = app._library.get(rig.voice)
        catalog = dict(voice.params) if voice is not None else {}
        data["voice_values"] = {**catalog, **rig.voice_params}
        data["unavailable"] = app._rig_unavailable(rig)
        data["active"] = self._is_active(rig)
        if data["active"]:
            data["effects"] = [
                {"uri": e.uri, "params": dict(e.params), "bypassed": e.bypassed}
                for e in app._engine.effects()
            ]
            data["voice_values"] = {
                **data["voice_values"], **app._engine.voice_params()
            }
            screen = app._effects_screen
            if screen is not None and app.screen is screen:
                data["trim_db"] = screen.trim_slider.value
                data["fixed_velocity"] = screen.fixed_velocity
        return data

    def state(self) -> dict:
        app = self._app
        active = self._active_rig()
        return {
            "sets": [
                {
                    "id": s.id,
                    "name": s.name,
                    "rigs": [self.snapshot(r) for r in s.rigs],
                }
                for s in app._sets.sets
            ],
            "active_set": app._active_set.id if app._active_set else None,
            "active_rig": active.id if active else None,
            "busy": self.busy,
            "fixed_velocity_available": app._engine.fixed_velocity_available(),
            "trim_range_db": TRIM_RANGE_DB,
        }

    def catalog(self) -> dict:
        app = self._app
        return {
            "voices": [
                {"name": v.name, "category": v.category,
                 "unavailable": v.unavailable_reason}
                for v in sorted(app._library.values(),
                                key=lambda v: (v.category, v.name))
            ],
            "effects": [
                {"name": e.name, "uri": e.uri, "category": e.category,
                 "note": e.note, "unavailable": e.unavailable_reason}
                for e in app._catalog
            ],
        }

    def export(self) -> list[dict]:
        """Every set, in the store's own format, with the active rig as it is
        now rather than as last saved."""
        out = []
        for s in self._app._sets.sets:
            rigs = []
            for r in s.rigs:
                snap = self.snapshot(r)
                rigs.append(Rig.from_dict(snap).to_dict())
            out.append({"id": s.id, "name": s.name, "rigs": rigs})
        return out

    # --- controls: safe off the UI thread -------------------------------
    #
    # Reading a plugin's controls runs lv2info, a subprocess that scans every
    # installed plugin. It touches no app state, so it runs on the request's
    # own thread rather than stalling the frame loop, and is cached: a plugin's
    # controls don't change while the UI is running.

    def _cached(self, key: str, read) -> dict:
        with self._controls_lock:
            if key in self._controls_cache:
                return self._controls_cache[key]
        ports, uri = read()
        controls = controls_for(ports)
        layout = eq_layout_for(uri, {c.symbol for c in controls}) if uri else None
        result = {
            "controls": [c.to_dict() for c in controls],
            "eq": layout.to_dict() if layout else None,
        }
        with self._controls_lock:
            self._controls_cache[key] = result
        return result

    def effect_controls(self, uri: str) -> dict:
        return self._cached(
            "effect:" + uri, lambda: (self._app._engine.effect_controls(uri), uri)
        )

    def voice_controls(self, name: str) -> dict:
        voice = self._app._library.get(name)
        if voice is None:
            raise _not_found(f"voice '{name}'")
        return self._cached(
            "voice:" + name,
            lambda: (self._app._engine.voice_controls(voice), voice.uri),
        )

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _set(self, set_id: str) -> SongSet:
        song_set = self._app._sets.get(set_id)
        if song_set is None:
            raise _not_found("set")
        return song_set

    def _rig(self, rig_id: str) -> tuple[SongSet, Rig]:
        found = self._app._sets.find_rig(rig_id)
        if found is None:
            raise _not_found("rig")
        return found

    def _voice(self, name: str):
        voice = self._app._library.get(name)
        if voice is None:
            raise _not_found(f"voice '{name}'")
        return voice

    def _effect_uri(self, uri: str) -> str:
        if not any(e.uri == uri for e in self._app._catalog):
            raise _not_found(f"effect '{uri}'")
        return uri

    def _saved(self, active_rig_changed: bool = False) -> None:
        self._app._sets.save()
        self._app._after_remote_edit(active_rig_changed)

    def _require_idle(self) -> None:
        if self.busy:
            raise EditorError("the instrument is still loading", status=409)

    # ------------------------------------------------------------------
    # Sets
    # ------------------------------------------------------------------

    def create_set(self, name: str) -> dict:
        song_set = self._app._sets.create(_name(name, "New Set"))
        self._saved()
        return {"id": song_set.id}

    def rename_set(self, set_id: str, name: str) -> None:
        self._set(set_id).name = _name(name)
        self._saved()

    def delete_set(self, set_id: str) -> None:
        """Deletes the rigs in it too. If it's the set being played, what's
        sounding keeps sounding — as it does from the touchscreen."""
        app = self._app
        song_set = self._set(set_id)
        was_active = app._active_set is not None and app._active_set.id == set_id
        playing_here = any(self._is_active(r) for r in song_set.rigs)
        app._sets.remove(set_id)
        if was_active:
            app._active_set = None
            app._home.set_active(None)
            app._rig_screen.set_rigs([], title="No set")
            if app.screen is app._rig_screen:
                app._show_home()
        if playing_here:
            app._rig_screen.rig_removed(self._active_rig().id)
        self._saved(active_rig_changed=playing_here)

    def move_set(self, set_id: str, to: int) -> None:
        sets = self._app._sets
        index = next(i for i, s in enumerate(sets.sets) if s.id == self._set(set_id).id)
        sets.move(index, int(to))
        self._saved()

    # ------------------------------------------------------------------
    # Rigs
    # ------------------------------------------------------------------

    def create_rig(self, set_id: str, voice: str, name: str = "") -> dict:
        """A new rig: the instrument, nothing on it. Not loaded — it plays when
        it's chosen at the instrument."""
        song_set = self._set(set_id)
        self._voice(voice)
        rig = song_set.create_from_voice(voice)
        if name.strip():
            rig.name = name.strip()
        self._saved()
        return {"id": rig.id}

    def update_rig(
        self,
        rig_id: str,
        name: str | None = None,
        voice: str | None = None,
        trim_db: float | None = None,
        fixed_velocity: bool | None = None,
    ) -> dict:
        song_set, rig = self._rig(rig_id)
        active = self._is_active(rig)
        pending = False
        if name is not None:
            rig.name = _name(name)
        if trim_db is not None:
            rig.trim_db = float(trim_db)
            if active:
                self._app._engine.set_rig_trim(rig.trim_db)
        if fixed_velocity is not None:
            rig.fixed_velocity = bool(fixed_velocity)
            if active and self._app._engine.fixed_velocity_available():
                self._app._engine.set_fixed_velocity(rig.fixed_velocity)
        if voice is not None and voice != rig.voice:
            new_voice = self._voice(voice)
            if active:
                self._require_idle()
            rig.voice = voice
            # The patch described another instrument's controls; see
            # app._swap_instrument.
            rig.voice_params = {}
            if active:
                pending = True
                self._in_background(lambda: self._app._engine.load_voice(new_voice))
        song_set.replace(rig)
        self._saved(active_rig_changed=active)
        return {"pending": pending}

    def delete_rig(self, rig_id: str) -> None:
        song_set, rig = self._rig(rig_id)
        active = self._is_active(rig)
        song_set.remove(rig_id)
        if active:
            # What's playing keeps playing; the screen stops claiming a rig
            # that no longer exists.
            self._app._rig_screen.rig_removed(rig_id)
        self._saved(active_rig_changed=active)

    def move_rig(self, rig_id: str, to: int) -> None:
        song_set, rig = self._rig(rig_id)
        song_set.move(song_set.rigs.index(rig), int(to))
        self._saved()

    def copy_rig(self, rig_id: str, set_id: str) -> dict:
        """A copy, not a reference: a rig belongs to one set (see set.py), so
        the copy is free to change without touching the original."""
        _, rig = self._rig(rig_id)
        target = self._set(set_id)
        copy = Rig.from_dict({**self.snapshot(rig), "id": new_id()})
        copy.name = target.unique_name(copy.name)
        target.rigs.append(copy)
        self._saved()
        return {"id": copy.id}

    def set_voice_param(self, rig_id: str, symbol: str, value: float) -> None:
        _, rig = self._rig(rig_id)
        value = float(value)
        if self._is_active(rig):
            self._require_idle()
            self._app._engine.set_voice_param(symbol, str(value))
            self._app._persist_active_rig()
            self._saved(active_rig_changed=True)
            return
        voice = self._app._library.get(rig.voice)
        catalog = voice.params if voice is not None else {}
        # Stored as a difference from the catalog, as _sync_active_rig does.
        if catalog.get(symbol) == value:
            rig.voice_params.pop(symbol, None)
        else:
            rig.voice_params[symbol] = value
        self._saved()

    # ------------------------------------------------------------------
    # A rig's effects, addressed by position in its chain
    # ------------------------------------------------------------------

    def _index(self, effects: list, index) -> int:
        index = int(index)
        if not 0 <= index < len(effects):
            raise _not_found("effect")
        return index

    def add_effect(self, rig_id: str, uri: str, index: int | None = None) -> dict:
        song_set, rig = self._rig(rig_id)
        uri = self._effect_uri(uri)
        if not self._is_active(rig):
            at = len(rig.effects) if index is None else max(0, int(index))
            rig.effects.insert(at, RigEffect(uri=uri))
            self._saved()
            return {"pending": False}
        self._require_idle()
        engine = self._app._engine
        self._in_background(
            lambda: engine.add_effect(uri, None if index is None else int(index))
        )
        return {"pending": True}

    def remove_effect(self, rig_id: str, index: int) -> dict:
        _, rig = self._rig(rig_id)
        if not self._is_active(rig):
            del rig.effects[self._index(rig.effects, index)]
            self._saved()
            return {"pending": False}
        self._require_idle()
        engine = self._app._engine
        instance = engine.effects()[self._index(engine.effects(), index)].instance
        self._in_background(lambda: engine.remove_effect(instance))
        return {"pending": True}

    def move_effect(self, rig_id: str, index: int, to: int) -> None:
        _, rig = self._rig(rig_id)
        if not self._is_active(rig):
            source = self._index(rig.effects, index)
            effect = rig.effects.pop(source)
            rig.effects.insert(max(0, min(int(to), len(rig.effects))), effect)
            self._saved()
            return
        self._require_idle()
        engine = self._app._engine
        self._index(engine.effects(), index)
        engine.move_effect(int(index), int(to))
        self._app._persist_active_rig()
        self._saved(active_rig_changed=True)

    def update_effect(
        self,
        rig_id: str,
        index: int,
        bypassed: bool | None = None,
        params: dict[str, float] | None = None,
    ) -> None:
        _, rig = self._rig(rig_id)
        if not self._is_active(rig):
            effect = rig.effects[self._index(rig.effects, index)]
            if bypassed is not None:
                effect.bypassed = bool(bypassed)
            for symbol, value in (params or {}).items():
                effect.params[symbol] = float(value)
            self._saved()
            return
        self._require_idle()
        engine = self._app._engine
        live = engine.effects()
        effect = live[self._index(live, index)]
        if bypassed is not None:
            engine.set_effect_bypass(effect.instance, bool(bypassed))
        for symbol, value in (params or {}).items():
            engine.set_effect_param(effect.instance, symbol, str(float(value)))
        self._app._persist_active_rig()
        self._saved(active_rig_changed=True)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def import_sets(self, data) -> dict:
        """Add the sets in an export, alongside the ones already here. See
        SetLibrary.import_sets — the USB restore goes through it too."""
        try:
            added = self._app._sets.import_sets(data)
        except ValueError as exc:
            raise EditorError(str(exc)) from exc
        self._app._after_remote_edit(False)
        return {"added": len(added)}

    # ------------------------------------------------------------------

    def _in_background(self, work) -> None:
        """Slow engine work off the UI thread, then save and refresh on it."""
        app = self._app
        self.busy = True

        def run() -> None:
            try:
                work()
            except Exception:
                logger.exception("editor engine operation failed")
            finally:
                def finish() -> None:
                    self.busy = False
                    app._persist_active_rig()
                    self._saved(active_rig_changed=True)

                app.call_soon(finish)

        app._in_background(run)


def _name(name, default: str = "") -> str:
    name = str(name or "").strip()
    if not name and not default:
        raise EditorError("a name can't be empty")
    return name or default
