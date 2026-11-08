// Akouo — Reinhören und die Großansicht eines Tracks.
//
// Rückmeldung vom 08.11.: Die Kurve war zu klein, um Anfang und Ende sicher zu setzen, und
// hineinhören ging gar nicht. Hier liegt beides: ein Abspieler, der Stücke der Aufnahme
// holt (auch während sie läuft), und eine Ansicht, in der man einen Track groß sieht,
// seine Kanten zieht, ihn teilt, Lücken markiert und den Titel einträgt.
//
// Kein Framework außer Alpine, keine Bibliothek fürs Zeichnen: ein Canvas, Pointer Events.
(function () {
  'use strict';

  function zeit(s, zehntel) {
    if (s === null || s === undefined || isNaN(s)) return '–';
    const neg = s < 0;
    s = Math.abs(s);
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sek = s % 60;
    const pad = n => String(n).padStart(2, '0');
    const sekText = zehntel ? sek.toFixed(1).padStart(4, '0') : pad(Math.floor(sek));
    return (neg ? '−' : '') + (h ? `${h}:${pad(m)}:${sekText}` : `${pad(m)}:${sekText}`);
  }
  window.akouoZeit = zeit;

  function toast(text) {
    window.dispatchEvent(new CustomEvent('toast', {detail: text}));
  }

  async function anfrage(url, opts) {
    const r = await fetch(url, opts);
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || (tr('Fehler') + ' ' + r.status));
    return d;
  }

  function json(methode, daten) {
    return {method: methode, headers: {'Content-Type': 'application/json'},
            body: daten === undefined ? undefined : JSON.stringify(daten)};
  }

  // ── Reinhören ─────────────────────────────────────────────────────────────
  document.addEventListener('alpine:init', () => {
    Alpine.store('hoeren', {
      laeuft: false,
      session: null,
      von: 0,
      bis: 0,
      position: null,         // Sekunde in der Aufnahme, die gerade klingt
      ueberDante: false,
      danteBereit: false,
      _audio: null,
      _takt: null,
      _danteStart: 0,

      init() {
        try { this.ueberDante = localStorage.getItem('hoeren:dante') === '1'; } catch (_) { }
        this.pruefeDante();
      },
      async pruefeDante() {
        try {
          const d = await fetch('/api/sessions/rueckspiel').then(r => r.json());
          this.danteBereit = Boolean(d.eingerichtet);
        } catch (_) { this.danteBereit = false; }
      },
      setDante(an) {
        this.ueberDante = Boolean(an);
        try { localStorage.setItem('hoeren:dante', this.ueberDante ? '1' : '0'); } catch (_) { }
      },

      spielen(sessionId, von, bis) {
        if (!sessionId) return;
        von = Math.max(0, von);
        if (bis <= von) bis = von + 10;
        if (this.ueberDante && this.danteBereit) { this._dante(sessionId, von, bis); return; }
        // Wie beim Mithören: nur mit Kopfhörer, sonst landet der Ton wieder in der Aufnahme
        let gewarnt = false;
        try { gewarnt = Boolean(sessionStorage.getItem('monitor:warned')); } catch (_) { }
        const monitor = Alpine.store('monitor');
        if (!gewarnt && monitor) {
          monitor.nachWarnung = () => this._spielen(sessionId, von, bis);
          monitor.warningOpen = true;
          return;
        }
        this._spielen(sessionId, von, bis);
      },

      _spielen(sessionId, von, bis) {
        this.stop();
        const monitor = Alpine.store('monitor');
        if (monitor && monitor.playing) monitor.stop();     // nicht zwei Tonquellen gleichzeitig
        const a = new Audio(`/api/sessions/${sessionId}/clip.mp3?von=${von.toFixed(2)}&bis=${bis.toFixed(2)}&t=${Date.now()}`);
        a.volume = monitor ? monitor.volume : 0.4;
        a.addEventListener('ended', () => this.stop());
        a.addEventListener('error', () => { if (this._audio === a) { this.stop(); toast(tr('Reinhören fehlgeschlagen')); } });
        this._audio = a;
        Object.assign(this, {laeuft: true, session: sessionId, von, bis, position: von});
        this._takt = setInterval(() => { if (this._audio) this.position = this.von + this._audio.currentTime; }, 100);
        a.play().catch(() => this.stop());
      },

      async _dante(sessionId, von, bis) {
        this.stop();
        try {
          await anfrage(`/api/sessions/${sessionId}/rueckspiel`, json('POST', {from_s: von, to_s: bis}));
        } catch (e) { toast(trf('Rückspiel über Dante: {fehler}', {fehler: e.message})); return; }
        Object.assign(this, {laeuft: true, session: sessionId, von, bis, position: von, _danteStart: Date.now()});
        this._takt = setInterval(() => {
          this.position = this.von + (Date.now() - this._danteStart) / 1000;
          if (this.position >= this.bis) this.stop();
        }, 100);
      },

      stop() {
        if (this._audio) {
          this._audio.pause();
          this._audio.src = '';
          this._audio = null;
        }
        if (this.laeuft && this.ueberDante && this.danteBereit) {
          fetch('/api/sessions/rueckspiel/stop', {method: 'POST'}).catch(() => { });
        }
        clearInterval(this._takt);
        this._takt = null;
        this.laeuft = false;
        this.position = null;
      },
    });
  });

  // ── Zeichnen ──────────────────────────────────────────────────────────────
  function leinwand(canvas) {
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * dpr);
    const h = Math.round(canvas.clientHeight * dpr);
    if (!w || !h) return null;
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, w, h);
    return {ctx, w, h, dpr};
  }

  // Hüllkurve: Spitzenpegel je Block, symmetrisch um die Mitte, eine Säule je Pixelspalte
  function huelle(ctx, w, h, kurve, von, bis, farbe) {
    if (!kurve || !kurve.werte.length || bis <= von) return;
    const boden = kurve.boden || -60;
    ctx.fillStyle = farbe;
    for (let x = 0; x < w; x++) {
      const t0 = von + (x / w) * (bis - von);
      const t1 = von + ((x + 1) / w) * (bis - von);
      const a = Math.max(0, Math.floor((t0 - kurve.von) / kurve.block));
      const b = Math.min(kurve.werte.length, Math.max(a + 1, Math.ceil((t1 - kurve.von) / kurve.block)));
      let spitze = null;
      for (let i = a; i < b; i++) if (spitze === null || kurve.werte[i] > spitze) spitze = kurve.werte[i];
      if (spitze === null) continue;
      const hoehe = Math.max(1, Math.max(0, (spitze - boden) / -boden) * h);
      ctx.fillRect(x, (h - hoehe) / 2, 1, hoehe);
    }
  }
  window.akouoHuelle = huelle;

  // Sinnvolle Abstände für die Zeitleiste
  function raster(spanne) {
    for (const s of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600]) {
      if (spanne / s <= 8) return s;
    }
    return 7200;
  }
  window.akouoRaster = raster;

  // ── Großansicht ───────────────────────────────────────────────────────────
  window.spurAnsicht = function () {
    return {
      offen: false,
      laedt: false,
      sid: null,
      daten: null,          // Antwort von /spuren
      idx: 0,
      start: 0,
      ende: 0,
      fenster: {von: 0, bis: 60},
      kurve: null,          // {von, block, werte, boden}
      cursor: null,
      titel: '',
      sprecher: '',
      teilTyp: '',
      neuTyp: '',
      lueckeVon: null,
      busy: false,
      kiLaeuft: false,
      geaendert: false,
      _zeiger: new Map(),
      _geste: null,
      _speichern: null,
      _kurveTimer: null,
      _speichernOffen: false,

      get track() { return this.daten ? this.daten.tracks[this.idx] : null; },
      get laeuft() { return Boolean(this.track && this.track.laeuft); },
      get aufnahmeLaeuft() { return this.daten && (this.daten.status === 'recording' || this.daten.status === 'paused'); },
      get laenge() {
        const d = this.daten;
        if (!d) return 0;
        const ende = Math.max(...d.tracks.map(t => t.end_seconds), 0);
        return Math.max(d.laenge_s || 0, ende);
      },
      zeit,

      init() {
        window.addEventListener('spur:oeffnen', e => this.oeffnen(e.detail));
        window.addEventListener('resize', () => this.offen && this.zeichnen());
      },

      async oeffnen({session_id, marker_id}) {
        this.sid = session_id;
        this.offen = true;
        this.cursor = null;
        this.lueckeVon = null;
        this.geaendert = false;
        await this.laden(marker_id);
      },

      schliessen() {
        clearTimeout(this._speichern);
        if (this._speichernOffen) this.kantenSpeichern();
        Alpine.store('hoeren').stop();
        this.offen = false;
        if (this.geaendert) window.dispatchEvent(new CustomEvent('spur:geaendert', {detail: {session_id: this.sid}}));
      },

      async laden(markerId) {
        this.laedt = true;
        try {
          this.daten = await anfrage(`/api/sessions/${this.sid}/spuren`);
          let i = this.daten.tracks.findIndex(t => t.marker_id === markerId);
          if (i < 0) i = Math.min(this.idx, this.daten.tracks.length - 1);
          this.waehlen(Math.max(0, i), markerId !== undefined);
        } catch (e) {
          toast(e.message);
          this.offen = false;
        } finally {
          this.laedt = false;
        }
      },

      waehlen(i, fensterNeu = true) {
        this.idx = i;
        const t = this.track;
        if (!t) return;
        this.start = t.start_seconds;
        this.ende = t.end_seconds;
        this.titel = t.titel;
        this.sprecher = t.sprecher;
        this.teilTyp = '';
        this.neuTyp = '';
        if (fensterNeu) {
          const rand = Math.max(6, (this.ende - this.start) * 0.08);
          this.fenster = {von: Math.max(0, this.start - rand), bis: this.ende + rand};
        }
        this.$nextTick(() => { this.zeichnen(); this.kurveHolen(); });
      },

      // ── Kurve ─────────────────────────────────────────────────────────────
      kurveHolen() {
        clearTimeout(this._kurveTimer);
        this._kurveTimer = setTimeout(() => this._kurveHolen(), 120);
      },
      async _kurveHolen() {
        const {von, bis} = this.fenster;
        const spanne = bis - von;
        // Etwa eine Säule je Bildpunkt; feiner als 50 ms liefert der Audio-Dienst nicht
        const breite = (this.$refs.gross && this.$refs.gross.clientWidth) || 800;
        const block = Math.max(50, Math.min(2000, Math.round(spanne * 1000 / breite / 25) * 25 || 50));
        const k = this.kurve;
        if (k && k.block * 1000 <= block && k.von <= von && k.von + k.werte.length * k.block >= Math.min(bis, this.laenge)) return;
        const rand = spanne * 0.5;
        const q = new URLSearchParams({session_id: this.sid, from_seconds: Math.max(0, von - rand).toFixed(2),
                                       to_seconds: (bis + rand).toFixed(2), block_ms: block});
        try {
          const d = await anfrage(`/api/sessions/levels?${q}`);
          this.kurve = {von: d.from_seconds, block: d.block_seconds, werte: d.levels || [], boden: d.floor_dbfs};
          this.zeichnen();
        } catch (_) { /* ohne Kurve geht es trotzdem */ }
      },

      // ── Zeichnen ──────────────────────────────────────────────────────────
      x(t, w) { return (t - this.fenster.von) / (this.fenster.bis - this.fenster.von) * w; },
      t(px, w) { return this.fenster.von + px / w * (this.fenster.bis - this.fenster.von); },

      zeichnen() {
        const canvas = this.$refs.gross;
        if (!canvas || !this.daten) return;
        const c = leinwand(canvas);
        if (!c) return;
        const {ctx, w, h, dpr} = c;
        const X = t => this.x(t, w);
        const kopf = 16 * dpr;

        // Nachbartracks grau, der eigene hell
        for (const [i, t] of this.daten.tracks.entries()) {
          if (i === this.idx) continue;
          ctx.fillStyle = 'rgba(255,255,255,0.05)';
          ctx.fillRect(X(t.start_seconds), kopf, Math.max(1, X(t.end_seconds) - X(t.start_seconds)), h - kopf);
          ctx.fillStyle = 'rgba(161,161,170,0.8)';
          ctx.font = `${11 * dpr}px ui-sans-serif, system-ui`;
          ctx.fillText(anzBeitrag(t.marker_type), Math.max(2, X(t.start_seconds) + 4 * dpr), kopf + 12 * dpr);
        }
        ctx.fillStyle = 'rgba(56,189,248,0.13)';
        ctx.fillRect(X(this.start), kopf, Math.max(1, X(this.ende) - X(this.start)), h - kopf);

        // Lücken im Track: schraffiert, sie fehlen im Export
        for (const l of (this.track ? this.track.luecken : [])) {
          const a = X(l.von_s), b = X(l.bis_s === null ? this.ende : l.bis_s);
          ctx.fillStyle = 'rgba(239,68,68,0.16)';
          ctx.fillRect(a, kopf, Math.max(1, b - a), h - kopf);
        }
        if (this.lueckeVon !== null) {
          ctx.fillStyle = 'rgba(239,68,68,0.7)';
          ctx.fillRect(X(this.lueckeVon), kopf, 2 * dpr, h - kopf);
        }

        // Pegel
        ctx.save();
        ctx.translate(0, kopf);
        huelle(ctx, w, h - kopf, this.kurve, this.fenster.von, this.fenster.bis, 'rgba(228,228,231,0.6)');
        ctx.restore();

        // Zeitleiste
        const schritt = raster(this.fenster.bis - this.fenster.von);
        ctx.fillStyle = 'rgba(161,161,170,0.9)';
        ctx.font = `${10 * dpr}px ui-monospace, monospace`;
        for (let s = Math.ceil(this.fenster.von / schritt) * schritt; s <= this.fenster.bis; s += schritt) {
          const px = X(s);
          ctx.fillRect(px, kopf - 4 * dpr, 1, 4 * dpr);
          ctx.fillText(zeit(s, schritt < 1), px + 2 * dpr, 10 * dpr);
        }

        // Griffe für Anfang und Ende
        const griff = (t, farbe) => {
          const px = X(t);
          ctx.fillStyle = farbe;
          ctx.fillRect(px - dpr, kopf, 2 * dpr, h - kopf);
          ctx.beginPath();
          ctx.arc(px, h - 12 * dpr, 9 * dpr, 0, Math.PI * 2);
          ctx.fill();
        };
        griff(this.start, '#38bdf8');
        if (!this.laeuft) griff(this.ende, '#38bdf8');

        if (this.cursor !== null) {
          ctx.fillStyle = '#fafafa';
          ctx.fillRect(X(this.cursor), kopf, 1 * dpr, h - kopf);
        }
        const pos = Alpine.store('hoeren').position;
        if (pos !== null && Alpine.store('hoeren').session === this.sid) {
          ctx.fillStyle = '#34d399';
          ctx.fillRect(X(pos), kopf, 2 * dpr, h - kopf);
        }
      },

      // ── Gesten: ziehen, verschieben, zwei Finger zoomen, tippen ──────────
      runter(e) {
        const canvas = this.$refs.gross;
        canvas.setPointerCapture(e.pointerId);
        this._zeiger.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
        if (this._zeiger.size === 2) {
          const [a, b] = [...this._zeiger.values()];
          this._geste = {art: 'zoom', abstand: Math.abs(a.x - b.x) || 1, fenster: {...this.fenster},
                         mitte: this.t((a.x + b.x) / 2, canvas.clientWidth)};
          return;
        }
        const w = canvas.clientWidth;
        const nah = px => Math.abs(this.x(px, w) - e.offsetX) < 22;
        let art = 'schieben';
        if (nah(this.start) && (this.laeuft || !nah(this.ende) ||
            Math.abs(this.x(this.start, w) - e.offsetX) < Math.abs(this.x(this.ende, w) - e.offsetX))) art = 'start';
        else if (!this.laeuft && nah(this.ende)) art = 'ende';
        this._geste = {art, x0: e.offsetX, fenster: {...this.fenster}, bewegt: false};
      },
      ziehen(e) {
        if (!this._zeiger.has(e.pointerId) || !this._geste) return;
        this._zeiger.set(e.pointerId, {x: e.offsetX, y: e.offsetY});
        const w = this.$refs.gross.clientWidth;
        const g = this._geste;
        if (g.art === 'zoom' && this._zeiger.size === 2) {
          const [a, b] = [...this._zeiger.values()];
          const faktor = g.abstand / (Math.abs(a.x - b.x) || 1);
          this.zoomUm(g.mitte, faktor, g.fenster);
          return;
        }
        if (Math.abs(e.offsetX - g.x0) > 4) g.bewegt = true;
        if (!g.bewegt) return;
        if (g.art === 'start') {
          this.start = Math.min(this.t(e.offsetX, w), this.laeuft ? Infinity : this.ende - 0.5);
          this.start = Math.max(0, this.start);
        } else if (g.art === 'ende') {
          this.ende = Math.max(this.t(e.offsetX, w), this.start + 0.5);
        } else {
          const dt = (e.offsetX - g.x0) / w * (g.fenster.bis - g.fenster.von);
          this.setzeFenster(g.fenster.von - dt, g.fenster.bis - dt);
        }
        this.zeichnen();
      },
      hoch(e) {
        this._zeiger.delete(e.pointerId);
        const g = this._geste;
        if (!g || this._zeiger.size > 0) { if (this._zeiger.size === 0) this._geste = null; return; }
        this._geste = null;
        if (g.art === 'zoom') { this.kurveHolen(); return; }
        if (!g.bewegt) {
          this.cursor = this.t(e.offsetX, this.$refs.gross.clientWidth);
          this.zeichnen();
          return;
        }
        if (g.art === 'start' || g.art === 'ende') this.kantenBald();
        else this.kurveHolen();
      },
      rad(e) {
        // Trackpad und Maus: Strg/Pinch zoomt, sonst seitlich verschieben
        const w = this.$refs.gross.clientWidth;
        if (e.ctrlKey || Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
          this.zoomUm(this.t(e.offsetX, w), Math.exp(e.deltaY * 0.01));
        } else {
          const dt = e.deltaX / w * (this.fenster.bis - this.fenster.von);
          this.setzeFenster(this.fenster.von + dt, this.fenster.bis + dt);
        }
        this.zeichnen();
        this.kurveHolen();
      },

      setzeFenster(von, bis) {
        const spanne = Math.max(1, Math.min(bis - von, Math.max(this.laenge, 60)));
        von = Math.max(0, Math.min(von, Math.max(0, this.laenge - spanne)));
        this.fenster = {von, bis: von + spanne};
      },
      zoomUm(mitte, faktor, basis) {
        basis = basis || this.fenster;
        const von = mitte - (mitte - basis.von) * faktor;
        const bis = mitte + (basis.bis - mitte) * faktor;
        this.setzeFenster(von, bis);
        this.zeichnen();
      },
      zoom(faktor) {
        const mitte = this.cursor !== null ? this.cursor : (this.fenster.von + this.fenster.bis) / 2;
        this.zoomUm(mitte, faktor);
        this.kurveHolen();
      },
      zeige(art) {
        const um = (t, halb) => this.setzeFenster(t - halb, t + halb);
        if (art === 'track') {
          const rand = Math.max(6, (this.ende - this.start) * 0.08);
          this.setzeFenster(this.start - rand, this.ende + rand);
        } else if (art === 'anfang') um(this.start, 10);
        else if (art === 'ende') um(this.ende, 10);
        else if (art === 'alles') this.setzeFenster(0, this.laenge);
        this.zeichnen();
        this.kurveHolen();
      },

      // ── Kanten ────────────────────────────────────────────────────────────
      fein(was, delta) {
        if (was === 'start') this.start = Math.max(0, Math.min(this.start + delta, this.laeuft ? Infinity : this.ende - 0.5));
        else this.ende = Math.max(this.start + 0.5, this.ende + delta);
        this.zeichnen();
        this.kantenBald();
      },
      kantenBald() {
        this._speichernOffen = true;
        clearTimeout(this._speichern);
        this._speichern = setTimeout(() => this.kantenSpeichern(), 700);
      },
      async kantenSpeichern() {
        this._speichernOffen = false;
        const t = this.track;
        if (!t) return;
        const body = {start_s: +this.start.toFixed(3)};
        if (!this.laeuft) body.ende_s = +this.ende.toFixed(3);
        try {
          await anfrage(`/api/sessions/markers/${t.marker_id}/kanten`, json('PATCH', body));
          t.start_seconds = this.start;
          if (!this.laeuft) t.end_seconds = this.ende;
          this.geaendert = true;
        } catch (e) { toast(e.message); }
      },

      // ── Hören ─────────────────────────────────────────────────────────────
      hoeren(art) {
        const h = Alpine.store('hoeren');
        if (h.laeuft) { h.stop(); return; }
        const bis = this.laeuft ? this.laenge : this.ende;
        const bereiche = {
          anfang: [this.start - 2, this.start + 8],
          ende: [bis - 8, bis + 2],
          cursor: [this.cursor ?? this.start, (this.cursor ?? this.start) + 30],
          ganz: [this.start, bis],
        };
        const [von, b] = bereiche[art];
        h.spielen(this.sid, von, b);
        const zeichne = () => { this.zeichnen(); if (h.laeuft && this.offen) requestAnimationFrame(zeichne); };
        requestAnimationFrame(zeichne);
      },

      // ── Angaben ───────────────────────────────────────────────────────────
      async typSetzen(typ) {
        const t = this.track;
        if (!typ || typ === t.marker_type) return;
        try {
          await anfrage(`/api/sessions/markers/${t.marker_id}/type`, json('PATCH', {marker_type: typ}));
          t.marker_type = typ;
          this.geaendert = true;
          this.zeichnen();
        } catch (e) { toast(e.message); }
      },
      async angabenSpeichern() {
        const t = this.track;
        try {
          await anfrage(`/api/sessions/${this.sid}/segments/${t.marker_id}/angaben`,
                        json('PATCH', {titel: this.titel, sprecher: this.sprecher}));
          t.titel = this.titel;
          t.sprecher = this.sprecher;
          t.titel_von_hand = Boolean(this.titel.trim());
          this.geaendert = true;
        } catch (e) { toast(e.message); }
      },
      async kiAuswerten() {
        const t = this.track;
        this.kiLaeuft = true;
        try {
          const d = await anfrage(`/api/sessions/${this.sid}/segments/${t.marker_id}/insights`, {method: 'POST'});
          t.titel = this.titel = d.title || '';
          t.sprecher = this.sprecher = d.speaker || '';
          t.zusammenfassung = d.summary || '';
          t.ki = true;
          this.geaendert = true;
        } catch (e) { toast(trf('KI-Auswertung: {fehler}', {fehler: e.message})); }
        finally { this.kiLaeuft = false; }
      },

      // ── Teilen, Einfügen, Lücken ──────────────────────────────────────────
      async teilen() {
        if (this.cursor === null) return;
        this.busy = true;
        try {
          const d = await anfrage(`/api/sessions/markers/${this.track.marker_id}/teilen`,
                                  json('POST', {bei_s: +this.cursor.toFixed(3), marker_type: this.teilTyp || null}));
          if (d.hinweis) toast(d.hinweis);
          this.geaendert = true;
          this.cursor = null;
          await this.laden(d.hinten);
        } catch (e) { toast(e.message); }
        finally { this.busy = false; }
      },
      async einfuegen() {
        if (this.cursor === null || !this.neuTyp) return;
        const naechster = this.daten.tracks.map(t => t.start_seconds).filter(s => s > this.cursor + 0.5);
        const ende = Math.min(this.cursor + 30, naechster.length ? Math.min(...naechster) : Infinity,
                              this.laenge || Infinity);
        this.busy = true;
        try {
          const d = await anfrage(`/api/sessions/${this.sid}/markers`,
                                  json('POST', {marker_type: this.neuTyp, start_s: +this.cursor.toFixed(3),
                                                ende_s: +ende.toFixed(3)}));
          if (d.hinweis) toast(d.hinweis);
          this.geaendert = true;
          this.cursor = null;
          await this.laden(d.marker_id);
        } catch (e) { toast(e.message); }
        finally { this.busy = false; }
      },
      async lueckeMarkieren() {
        if (this.cursor === null) return;
        if (this.lueckeVon === null) { this.lueckeVon = this.cursor; this.zeichnen(); return; }
        const von = Math.min(this.lueckeVon, this.cursor), bis = Math.max(this.lueckeVon, this.cursor);
        this.lueckeVon = null;
        try {
          await anfrage(`/api/sessions/markers/${this.track.marker_id}/luecken`, json('POST', {von_s: von, bis_s: bis}));
          this.geaendert = true;
          await this.laden(this.track.marker_id);
        } catch (e) { toast(e.message); this.zeichnen(); }
      },
      async lueckeWeg(l) {
        try {
          await anfrage(`/api/sessions/markers/${this.track.marker_id}/luecken/${l.id}`, {method: 'DELETE'});
          this.geaendert = true;
          await this.laden(this.track.marker_id);
        } catch (e) { toast(e.message); }
      },

      blaettern(richtung) {
        const i = this.idx + richtung;
        if (!this.daten || i < 0 || i >= this.daten.tracks.length) return;
        if (this._speichernOffen) { clearTimeout(this._speichern); this.kantenSpeichern(); }
        this.cursor = null;
        this.lueckeVon = null;
        this.waehlen(i);
      },
    };
  };
})();
