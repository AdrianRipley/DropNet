# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic paired campaign and entirely offline reporting, standard library only."""
from dataclasses import asdict
from pathlib import Path
from collections import defaultdict
import csv
import hashlib
import html
import json
import math
import platform
import random
import sqlite3
import statistics
import sys
import tempfile

from .core import Contact, Message, Simulator, Store, METADATA_BYTES

ASSUMPTIONS = {
    'schema_version': 3,
    'model': 'offline discrete events; synthetic contacts, no calibrated radio measurements',
    'metadata_bytes': METADATA_BYTES,
    'wire_storage_bytes': 'UTF-8 payload bytes + fixed 96 assumed metadata bytes; IDs abstracted into fixed metadata',
    'transport': 'full messages only; sequential half-duplex per contact; constant assumed bytes/second; baseline has no setup/control/ack overhead; inventory adds two explicitly costed frames',
    'loss': 'independent Bernoulli per full attempt; entire bytes and transmission time consumed on failure',
    'loss_coupling': 'same loss seed across policies; draws in attempt order, NOT identical per-contact or per-message outcomes',
    'ordering': 'completion before generation before contact at equal times; stable input order within event class; sender a before b, queue creation then ID',
    'overlap': 'contacts starting while either node transmits are declined, not deferred; no setup or idle occupancy; one candidate list snapshot per contact',
    'admission': 'strict completion time < expiry; expire before admission; relay drop-new if full; destination delivers without queue storage',
    'hop': 'source hops=0; every successful network traversal including destination increments hops; no traversal beyond hop_limit',
    'forwarding': 'baselines unchanged; inventory exchanges contact-start queue UNION live bounded seen IDs, including retained deliveries; filters snapshots only after both frames succeed; no global ledger or receiver oracle',
    'inventory': 'a->b then b->a; each frame 8 + sum(2 + max(32, UTF8 ID bytes)); independent loss per frame; abort on first loss or frame that cannot fit, no fallback; snapshots may become stale',
    'compact': '16-byte binary header + 8-byte salted SHA256 fingerprints; B=min(budget,floor(duration*rate)), control<=min(B//4,max(0,B-608)); allowance split by direction; empty summaries omitted; complete small summaries repeated twice unconditionally when both fit; otherwise one bounded sample; independently received peer summaries suppress matching candidates, missing summary falls back to epidemic; collision risk is nonzero, no authentication',
    'reservation': 'full bytes and size/rate seconds reserved at attempt start, including failed or pending control/data attempts; totals are reserved traffic, not emitted bytes at observation horizon',
    'dedup': 'expiry and count bounded; oldest-admitted eviction; retained queue IDs also suppress duplicates; after eviction app redelivery is possible',
    'persistence': 'SQLite on disk with default synchronous/rollback journal; transactions; queue, dedup and audit survive clean close/reopen; power failure not tested',
    'storage': 'capacity bounds logical queued payload+metadata only; seen count separately bounded; SQLite overhead and append-only delivery audit are outside queue quota',
    'latency': 'recipient completion minus creation, delivered only; survivor bias, no estimate for undelivered latency',
    'statistics': 'means and sample SD across independent seeded runs; Student t 95% intervals (df<=29 table; conservative df=29 for larger n), untruncated; paired differences use within-seed differences; no CI for n=1',
    'unimplemented': 'encryption, signatures, anonymity, public SOS trust, malicious nodes, BLE, batteries, physical mobility calibration, native Android port',
}


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def portable_command(arguments, root):
    """Record command arguments without publishing absolute local paths."""
    result = []
    for argument in arguments:
        option, separator, value = argument.partition('=')
        has_option_value = argument.startswith('-') and bool(separator)
        path = Path(value if has_option_value else argument)
        if path.is_absolute():
            try:
                value = str(path.relative_to(root))
            except ValueError:
                value = '<external-path>'
            argument = option + '=' + value if has_option_value else value
        result.append(argument)
    return result


def interval(values):
    """Student t critical value for supported independent-run sample counts."""
    n = len(values)
    mean = statistics.mean(values)
    sd = statistics.stdev(values) if n > 1 else None
    critical = [12.706205, 4.302653, 3.182446, 2.776445, 2.570582, 2.446912,
                2.364624, 2.306004, 2.262157, 2.228139, 2.200985, 2.178813,
                2.160369, 2.144787, 2.131450, 2.119905, 2.109816, 2.100922,
                2.093024, 2.085963, 2.079614, 2.073873, 2.068658, 2.063899,
                2.059539, 2.055529, 2.051831, 2.048407, 2.045229642]
    half = critical[min(n-2, 28)] * sd / math.sqrt(n) if n > 1 else None
    return dict(n=n, mean=mean, sd=sd, ci95=[mean-half, mean+half] if half is not None else None,
                minimum=min(values), maximum=max(values))


def inputs(seed, count, horizon=3600):
    rng = random.Random(seed)
    names = [f'N{i:02d}' for i in range(20)]
    messages = []
    for i in range(40):
        source, destination = rng.sample(names, 2)
        created = rng.uniform(0, horizon / 2)
        messages.append(Message(f'm{i:03d}', source, destination, created,
                                created + 1800, 'synthetic-' + str(i) + '-' + 'x' * rng.randint(8, 160)))
    contacts = []
    for _ in range(count):
        a, b = rng.sample(names, 2)
        contacts.append(Contact(rng.uniform(0, horizon - 8), a, b, 8, 128, 1024))
    return messages, sorted(contacts, key=lambda c: c.start)


def run_one(messages, contacts, policy, seed, capacity, horizon):
    # Ephemeral campaign stores are on disk; reproducible inputs and metrics are retained.
    with tempfile.TemporaryDirectory(prefix='dropnet-') as tmp:
        stores = {f'N{i:02d}': Store(Path(tmp) / f'{i}.sqlite', f'N{i:02d}', capacity, 256)
                  for i in range(20)}
        try:
            sim = Simulator(stores, policy, seed)
            sim.run(messages, contacts, horizon)
            deliveries = [row for store in stores.values() for row in store.deliveries()]
            ids = {row[0] for row in deliveries}
            assert len(ids) == len(deliveries), 'dedup unexpectedly exhausted in campaign'
            latency = [time-created for _, time, created in deliveries]
            attempts = sim.counters['attempted_bytes']
            in_flight = sum(event[3] == 'complete' for event in sim.events)
            assert sim.counters['attempts'] == sim.counters['completed_transfers'] + in_flight
            assert sim.counters['completed_transfers'] == sum(v for k, v in sim.counters.items() if k.startswith('outcome_'))
            pending_control = sim.pending_control_attempts
            assert sim.counters.get('control_attempts', 0) == sim.counters.get('control_completions', 0) + pending_control
            assert sim.counters.get('control_losses', 0) <= sim.counters.get('control_completions', 0)
            total_bytes = attempts + sim.counters.get('control_bytes', 0)
            total_seconds = sim.counters['transmission_seconds'] + sim.counters.get('control_seconds', 0)
            return dict(pending_control_attempts=pending_control,
                        total_attempted_bytes=total_bytes, total_transmission_seconds=total_seconds,
                        total_bytes_per_generated=total_bytes/len(messages),
                        total_bytes_per_delivered=total_bytes/len(ids) if ids else None,
                        generated=len(messages), delivered=len(ids), in_flight=in_flight,
                        delivery_fraction=len(ids)/len(messages),
                        median_latency=statistics.median(latency) if latency else None,
                        attempted_bytes=attempts, bytes_per_generated=attempts/len(messages),
                        bytes_per_delivered=attempts/len(ids) if ids else None,
                        peak_queue_bytes=max(s.peak for s in stores.values()),
                        counters=dict(sim.counters))
        finally:
            for store in stores.values():
                store.close()


def restart_demo(folder):
    folder.mkdir()
    stores = {n: Store(folder / f'{n}.sqlite', n) for n in 'ABC'}
    try:
        sim = Simulator(stores, trace=True)
        message = Message('restart-demo', 'A', 'C', 0, 100, 'Synthetic relay demo')
        sim.run([message], [Contact(1, 'A', 'B', 5, 128, 1024)], horizon=5)
        stores['B'].close()
        sim.log('store_closed', node='B')
        stores['B'] = Store(folder / 'B.sqlite', 'B')
        sim.log('store_reopened', node='B')
        sim.log('source_offline', node='A')
        sim.run(contacts=[Contact(10, 'B', 'C', 5, 128, 1024)], horizon=20)
        expected = 10 + message.size / 128
        assert stores['C'].deliveries() == [('restart-demo', expected, 0.0)]
        assert sim.counters['local_delivery'] == 1
        dump(folder / 'trace.json', sim.trace)
        dump(folder / 'result.json', dict(deliveries=stores['C'].deliveries(), expected_latency=expected,
                                          counters=dict(sim.counters)))
    finally:
        for store in stores.values():
            store.close()


def aggregate(runs):
    groups = defaultdict(list)
    for row in runs:
        groups[(row['contacts'], row['loss'], row['capacity'], row['policy'])].append(row)
    summaries = []
    for (count, loss, capacity, policy), rows in sorted(groups.items()):
        summary = dict(contacts=count, loss=loss, capacity=capacity, policy=policy)
        for key in ('delivery_fraction', 'bytes_per_generated', 'peak_queue_bytes', 'median_latency', 'bytes_per_delivered'):
            vals = [r[key] for r in rows if r[key] is not None]
            summary[key] = interval(vals) if vals else None
        if 'total_attempted_bytes' in rows[0]:
            for key in ('total_bytes_per_generated', 'total_bytes_per_delivered', 'total_transmission_seconds'):
                vals = [r[key] for r in rows if r[key] is not None]
                summary[key] = interval(vals) if vals else None
            # Include traffic from runs with zero deliveries in pooled efficiency.
            delivered_total = sum(r['delivered'] for r in rows)
            summary['pooled_total_bytes_per_delivered'] = (
                sum(r['total_attempted_bytes'] for r in rows) / delivered_total
                if delivered_total else None)
            summary['traffic_totals'] = dict(
                payload_bytes=sum(r['attempted_bytes'] for r in rows),
                total_bytes=sum(r['total_attempted_bytes'] for r in rows),
                control_bytes=sum(r['counters'].get('control_bytes', 0) for r in rows),
                duplicates=sum(r['counters'].get('duplicates', 0) for r in rows),
                failed_handshakes=sum(r['counters'].get('failed_handshakes', 0) for r in rows))
        summary['pooled_bytes_per_delivered'] = (sum(r['attempted_bytes'] for r in rows) /
                                                  sum(r['delivered'] for r in rows)
                                                  if sum(r['delivered'] for r in rows) else None)
        summaries.append(summary)
    paired = []
    conditions = sorted({(r['contacts'], r['loss'], r['capacity']) for r in runs})
    for count, loss, capacity in conditions:
        rows = [r for r in runs if (r['contacts'], r['loss'], r['capacity']) == (count, loss, capacity)]
        by_seed = defaultdict(dict)
        for r in rows:
            by_seed[r['seed']][r['policy']] = r
        differences = [v['epidemic']['delivery_fraction'] - v['direct']['delivery_fraction'] for v in by_seed.values()]
        convergence = [dict(seeds=n, **interval(differences[:n])) for n in (10, 20, len(differences))]
        inventory = {}
        if all('inventory' in v for v in by_seed.values()):
            pairs = list(by_seed.values())
            inventory = dict(
                inventory_minus_epidemic_delivery=interval([v['inventory']['delivery_fraction'] - v['epidemic']['delivery_fraction'] for v in pairs]),
                inventory_minus_epidemic_total_bytes=interval([v['inventory']['total_attempted_bytes'] - v['epidemic']['total_attempted_bytes'] for v in pairs]),
                relative_total_traffic_change=(sum(v['inventory']['total_attempted_bytes'] for v in pairs) / sum(v['epidemic']['total_attempted_bytes'] for v in pairs) - 1
                                             if sum(v['epidemic']['total_attempted_bytes'] for v in pairs) else None))
        if all('compact' in v for v in by_seed.values()):
            pairs = list(by_seed.values())
            for reference in ('epidemic', 'inventory'):
                if not all(reference in v for v in pairs):
                    continue
                prefix = 'compact_minus_' + reference
                inventory[prefix + '_delivery'] = interval([
                    v['compact']['delivery_fraction'] - v[reference]['delivery_fraction'] for v in pairs])
                inventory[prefix + '_total_bytes'] = interval([
                    v['compact']['total_attempted_bytes'] - v[reference]['total_attempted_bytes'] for v in pairs])
                denominator = sum(v[reference]['total_attempted_bytes'] for v in pairs)
                inventory[prefix + '_relative_traffic'] = (
                    sum(v['compact']['total_attempted_bytes'] for v in pairs) / denominator - 1
                    if denominator else None)
        paired.append(dict(**inventory,contacts=count, loss=loss, capacity=capacity,
                           epidemic_minus_direct=interval(differences), convergence=convergence))
    return summaries, paired


def svg_plot(summaries, capacity):
    rows = [s for s in summaries if s['capacity'] == capacity]
    width, height = 940, 70 + len(rows) * 28
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Delivery fractions with 95 percent intervals">',
           '<rect width="100%" height="100%" fill="white"/>']
    for i, row in enumerate(rows):
        y = 36 + i*28
        stat = row['delivery_fraction']
        label = f"{row['contacts']} contacts; loss {row['loss']}; {row['policy']}"
        color = {'epidemic': '#176a9e', 'direct': '#b24c18', 'inventory': '#25834a', 'compact': '#8a3fa0'}[row['policy']]
        x = 390 + 460*stat['mean']
        lo, hi = [390+460*v for v in stat['ci95']]
        out.extend([f'<text x="8" y="{y+5}" font-size="14">{html.escape(label)}</text>',
                    f'<line x1="{lo}" y1="{y}" x2="{hi}" y2="{y}" stroke="{color}" stroke-width="3"/>',
                    f'<circle cx="{x}" cy="{y}" r="4" fill="{color}"/>'])
    out.append(f'<text x="390" y="{height-8}" font-size="13">0</text><text x="850" y="{height-8}" font-size="13">1 delivered/generated</text></svg>')
    return ''.join(out)


def report(folder, summaries, paired, config):
    def format_interval(stat, scale=1):
        mean = scale * stat['mean']
        ci = stat['ci95']
        return (f'{mean:+.2f} [{scale*ci[0]:+.2f}, {scale*ci[1]:+.2f}]'
                if ci is not None else f'{mean:+.2f} [IC non disponibile]')

    rows = []
    for s in summaries:
        d = s['delivery_fraction']
        lat = s['median_latency']
        bpd = s['pooled_bytes_per_delivered']
        rows.append('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in (
            s['contacts'], s['loss'], s['capacity'], s['policy'],
            f"{d['mean']:.4f} ± {d['sd']:.4f}",
            f"[{d['ci95'][0]:.4f}, {d['ci95'][1]:.4f}]",
            f"{lat['mean']:.2f} (n={lat['n']})" if lat else 'undefined',
            f"{s['bytes_per_generated']['mean']:.2f}", f'{bpd:.2f}' if bpd is not None else 'undefined',
            f"{s['peak_queue_bytes']['mean']:.1f}")) + '</tr>')
    paired_rows = []
    for p in paired:
        d = p['epidemic_minus_direct']
        paired_rows.append(f"<tr><td>{p['contacts']}</td><td>{p['loss']}</td><td>{p['capacity']}</td>"
                           f"<td>{d['mean']:.4f}</td><td>[{d['ci95'][0]:.4f}, {d['ci95'][1]:.4f}]</td></tr>")
    inventory_rows = []
    compact_rows = []
    traffic_rows = []
    for p in paired:
        if 'inventory_minus_epidemic_delivery' in p:
            inventory_rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in (
                p['contacts'], p['loss'], p['capacity'],
                format_interval(p['inventory_minus_epidemic_delivery'], scale=100),
                format_interval(p['inventory_minus_epidemic_total_bytes']),
                f"{100*p['relative_total_traffic_change']:+.1f}%"
                if p['relative_total_traffic_change'] is not None else 'undefined')) + '</tr>')
        for reference in ('epidemic', 'inventory'):
            prefix = 'compact_minus_' + reference
            if prefix + '_delivery' in p:
                relative = p[prefix + '_relative_traffic']
                compact_rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in (
                    p['contacts'], p['loss'], p['capacity'], reference,
                    format_interval(p[prefix + '_delivery'], scale=100),
                    format_interval(p[prefix + '_total_bytes']),
                    f'{100*relative:+.1f}%' if relative is not None else 'undefined')) + '</tr>')
    for s in summaries:
        if 'traffic_totals' in s:
            t = s['traffic_totals']
            total_mean = s['total_bytes_per_generated']['mean']
            data_mean = s['bytes_per_generated']['mean']
            pooled_total = s['pooled_total_bytes_per_delivered']
            traffic_rows.append('<tr>' + ''.join('<td>' + html.escape(str(v)) + '</td>' for v in (
                s['contacts'], s['loss'], s['capacity'], s['policy'],
                f'{total_mean:.2f}', f'{data_mean:.2f}', f'{total_mean-data_mean:.2f}',
                f'{pooled_total:.2f}' if pooled_total is not None else 'undefined',
                t['duplicates'], t['failed_handshakes'])) + '</tr>')
    best = max(summaries, key=lambda s: s['delivery_fraction']['mean'])
    comparisons = [p for p in paired if 'inventory_minus_epidemic_delivery' in p]
    improved = sum(p['inventory_minus_epidemic_delivery']['mean'] > 0 for p in comparisons)
    worsened = sum(p['inventory_minus_epidemic_delivery']['mean'] < 0 for p in comparisons)
    inventory_assessment = (f'<p><strong>Scambio degli identificativi:</strong> la consegna media '
        f"aumenta in {improved} condizioni e diminuisce in {worsened} rispetto all'inoltro epidemico. "
        'Sono confronti delle medie osservate: consultare gli intervalli appaiati sotto. '
        'Un calo del traffico accompagnato da meno consegne non basta a dimostrare maggiore efficienza. '
        'Questa variante richiede due inventari completi ricevuti senza ritentativi nello stesso incontro: '
        'il costo e le perdite del controllo possono annullare il vantaggio dei duplicati evitati.</p>'
        if comparisons else '')
    compact_assessment = ''
    for reference in ('epidemic', 'inventory'):
        stats = [p['compact_minus_' + reference + '_delivery'] for p in paired
                 if 'compact_minus_' + reference + '_delivery' in p]
        if stats:
            # Equal total deliveries can leave ~1e-19 in floating paired means.
            # Treat only that numerical residue as zero, not statistical uncertainty.
            up, down = sum(s['mean'] > 1e-12 for s in stats), sum(s['mean'] < -1e-12 for s in stats)
            compact_assessment += (f'<p><strong>Compatto rispetto a {reference}:</strong> '
                f'consegna media maggiore in {up} condizioni, minore in {down}, uguale in {len(stats)-up-down}. '
                'Sono confronti descrittivi delle medie; gli intervalli appaiati sono sotto.</p>')
    if compact_assessment:
        compact_assessment += ('<p>Il protocollo compatto limita il controllo al 25% del budget effettivo, '
            'preservando anche spazio per un messaggio massimo del modello. Riepiloghi piccoli possono '
            'essere ripetuti senza attendere conferme; quelli mancanti non bloccano i dati. '
            'Gli elenchi parziali possono lasciare passare duplicati. Le impronte a 64 bit hanno rischio '
            'di collisione non nullo e non sono una protezione crittografica. Nessun risultato misura una radio reale.</p>')
    text = f'''<!doctype html><html lang="it"><meta charset="utf-8"><title>DropNet offline reference campaign</title>
<style>body{{font:16px system-ui;max-width:1200px;margin:36px auto;padding:0 20px;color:#203040}}table{{border-collapse:collapse;font-size:13px}}td,th{{padding:7px;border:1px solid #cbd4dc}}svg{{width:100%;height:auto}}pre{{white-space:pre-wrap}}code{{overflow-wrap:anywhere}}</style>
<h1>DropNet: esperimento sintetico offline</h1>
<p>{len(summaries)*config['seeds']} esecuzioni, {config['seeds']} semi appaiati per condizione, 20 nodi e 40 messaggi per esecuzione.
Nessun valore radio è misurato. Questo simulatore non è software Bluetooth né un servizio di emergenza.</p>
<h2>Interpretazione</h2><p>L'inoltro epidemico può raggiungere destinatari tramite incontri successivi,
ma consuma traffico per copie e duplicati. I limiti di memoria e la perdita possono ridurre la consegna.
Il risultato dipende esclusivamente dal modello sintetico dichiarato: non stima la copertura, l'autonomia
né l'affidabilità di telefoni reali. La maggiore frazione media osservata è {best['delivery_fraction']['mean']:.4f}
({best['policy']}, {best['contacts']} incontri, perdita {best['loss']}, memoria {best['capacity']} byte).</p>
{inventory_assessment}
{compact_assessment}
<p>Le latenze includono solo i messaggi consegnati: selezione dei sopravvissuti. Una latenza minore può
riflettere la mancata consegna dei messaggi più difficili. Gli intervalli descrivono la variabilità tra semi,
non l'incertezza sul mondo reale. Non si presume che la politica epidemica domini sempre quella diretta.</p>
<h2>Frazione consegnata: media e intervallo 95%</h2>
{''.join('<h3>Capacità per nodo: '+str(c)+' byte</h3>'+svg_plot(summaries,c) for c in config['capacities'])}
<h2>Metriche aggregate</h2><p>SD campionaria tra esecuzioni. Latenza: media delle mediane per esecuzione (secondi);
n esclude le esecuzioni senza consegne. Byte/consegnato: rapporto dei totali; undefined se nessuna consegna.
Picco: media dei massimi per nodo per esecuzione; tutti i valori individuali sono in runs.json/CSV.</p>
<table><tr><th>Incontri</th><th>Perdita</th><th>Capacità</th><th>Politica</th><th>Consegna ± SD</th><th>IC 95%</th><th>Latenza</th><th>Byte DATI/generato</th><th>Byte DATI/consegnato</th><th>Picco byte</th></tr>{''.join(rows)}</table>
<h2>Confronti appaiati: epidemica − diretta</h2><table><tr><th>Incontri</th><th>Perdita</th><th>Capacità</th><th>Differenza</th><th>IC 95%</th></tr>{''.join(paired_rows)}</table>
<p>paired.json contiene anche le stime cumulative a 10, 20 e 30 semi (diagnostica, non prova di convergenza).</p>
<h2>Inventario − epidemica: confronti appaiati</h2>
<p>L'inventario paga entrambi i frame e può ridurre la consegna. Delta byte include tutti i tentativi,
anche frame falliti. Variazione relativa = rapporto dei totali − 1, non media dei rapporti.</p>
<table><tr><th>Incontri</th><th>Perdita</th><th>Capacità</th><th>Delta consegna: punti % [IC 95%]</th><th>Delta byte TOTALI/esecuzione [IC 95%]</th><th>Variazione relativa traffico totale</th></tr>{''.join(inventory_rows)}</table>
<h2>Compatto rispetto alle due versioni precedenti</h2>
<p>Stessi messaggi e incontri, 30 semi appaiati. Le perdite sono estratte per tentativo;
non sono identiche tra politiche che effettuano numeri diversi di tentativi. Il traffico include dati,
riepiloghi, ripetizioni e tentativi persi. Non sono state selezionate soltanto le condizioni favorevoli.</p>
<table><tr><th>Incontri</th><th>Perdita</th><th>Capacità</th><th>Riferimento</th><th>Delta consegna: punti % [IC 95%]</th><th>Delta byte TOTALI/esecuzione [IC 95%]</th><th>Variazione relativa traffico totale</th></tr>{''.join(compact_rows)}</table>
<h2>Traffico totale e componenti per messaggio</h2>
<p>Byte DATI include testo e 96 byte di metadati; byte TOTALI include anche il controllo,
le perdite e i duplicati. Le colonne /generato sono medie sulle esecuzioni; TOTALI/consegnato
è il rapporto tra tutti i byte tentati e tutte le consegne, includendo il traffico delle
esecuzioni senza consegne. Duplicati e handshake falliti sono conteggi cumulativi sui semi.
I totali assoluti di dati, controllo e traffico sono conservati in summary.json.</p>
<table><tr><th>Incontri</th><th>Perdita</th><th>Capacità</th><th>Politica</th><th>Byte TOTALI/generato</th><th>Byte DATI/generato</th><th>Byte controllo/generato</th><th>Byte TOTALI/consegnato</th><th>Duplicati</th><th>Handshake falliti</th></tr>{''.join(traffic_rows)}</table>
<h2>Assunzioni e schema</h2><pre>{html.escape(json.dumps(ASSUMPTIONS, indent=2, ensure_ascii=False))}</pre>
<h2>Riproducibilità</h2><p>config.json conserva comando e ambiente; manifest.json contiene gli SHA256.
inputs/ conserva tutti gli eventi esogeni, demo/ contiene SQLite e traccia del riavvio.
I contatori non elencati in un dizionario valgono zero. Nessun accesso alla rete è necessario.</p></html>'''
    (folder / 'report.html').write_text(text, encoding='utf-8')


def campaign(folder, seeds=30):
    if seeds < 30:
        raise ValueError('full campaign requires at least 30 paired seeds')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'inputs').mkdir()
    root = Path(__file__).resolve().parents[1]
    paths = [root / name for name in ('SPEC.md', 'README.md', 'PUBLICATION_NOTES.md',
                                      'SOURCE_PROVENANCE.json', 'fixtures/baselines/README.md')]
    for directory in ('dropnet', 'tests', 'scripts'):
        # Generated validators in old output folders are not project source.
        paths.extend(sorted((root / directory).glob('*.py')))
    paths.extend(sorted((root / 'tests' / 'fixtures').glob('*.json')))
    paths.extend(sorted((root / 'fixtures' / 'baselines').rglob('*.json')))
    dump(folder / 'manifest.json', {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    config = dict(schema_version=3, seeds=seeds, seed_values=list(range(seeds)),
                  contact_counts=[40, 100, 240], losses=[0.0, 0.25, 0.75], capacities=[2048, 65536],
                  nodes=20, messages=40, horizon=3600, ttl=1800, rate=128, duration=8, budget=1024,
                  hop_limit=8, dedup_capacity=256, policies=['epidemic', 'direct', 'inventory', 'compact'],
                  loss_seed='100000 + exogenous seed', generator='random.Random(seed); messages first, contacts second',
                  command=portable_command(sys.argv, root), python=sys.version,
                  platform=platform.platform(), sqlite=sqlite3.sqlite_version,
                  assumptions=ASSUMPTIONS)
    dump(folder / 'config.json', config)
    restart_demo(folder / 'demo')
    runs = []
    for count in config['contact_counts']:
        for seed in range(seeds):
            messages, base_contacts = inputs(seed, count)
            dump(folder / 'inputs' / f'contacts{count}_seed{seed}.json',
                 dict(seed=seed, messages=[asdict(m) for m in messages],
                      contacts=[asdict(c) for c in base_contacts]))
            for loss in config['losses']:
                contacts = [Contact(**(asdict(c) | {'loss': loss})) for c in base_contacts]
                for capacity in config['capacities']:
                    for policy in config['policies']:
                        metrics = run_one(messages, contacts, policy, 100000+seed, capacity, config['horizon'])
                        runs.append(dict(seed=seed, contacts=count, loss=loss, capacity=capacity, policy=policy, **metrics))
        print(f'Completed contacts={count}: {len(runs)} runs', flush=True)
        dump(folder / 'runs.json', runs)
    with (folder / 'runs.csv').open('w', newline='', encoding='utf-8') as handle:
        flat = [dict({k: v for k, v in r.items() if k != 'counters'},
                     **{'counter_' + k: v for k, v in r['counters'].items()}) for r in runs]
        fields = sorted({k for r in flat for k in r})
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(flat)
    summaries, paired = aggregate(runs)
    dump(folder / 'summary.json', summaries)
    dump(folder / 'paired.json', paired)
    report(folder, summaries, paired, config)
    baseline_path = root / 'fixtures/baselines/v0.1/runs.json'
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    key = lambda r: (r['contacts'], r['seed'], r['loss'], r['capacity'], r['policy'])
    current = {key(r): r for r in runs}
    mismatches = [dict(key=key(old), field=k, old=v, new=current[key(old)].get(k))
                  for old in baseline for k, v in old.items() if current[key(old)].get(k) != v]
    dump(folder / 'baseline_comparison.json', dict(compared_runs=len(baseline), mismatches=mismatches,
         baseline=str(baseline_path.relative_to(root)), sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest()))
    assert not mismatches, 'legacy baseline metrics changed'
    previous_path = root / 'fixtures/baselines/v0.2/runs.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8'))
    mismatches = [dict(key=key(old), field=k, old=v, new=current[key(old)].get(k))
                  for old in previous for k, v in old.items() if current[key(old)].get(k) != v]
    old_inputs = root / 'fixtures/baselines/inputs'
    input_files = list(old_inputs.glob('*.json'))
    changed_inputs = [p.name for p in input_files if p.read_bytes() != (folder / 'inputs' / p.name).read_bytes()]
    dump(folder / 'previous_version_comparison.json', dict(compared_runs=len(previous), mismatches=mismatches,
         baseline=str(previous_path.relative_to(root)), sha256=hashlib.sha256(previous_path.read_bytes()).hexdigest(),
         compared_inputs=len(input_files), changed_inputs=changed_inputs))
    assert not mismatches and not changed_inputs, 'v0.2 baseline metrics or inputs changed'
    dump(folder / 'completion.json', dict(status='complete', runs=len(runs), conditions=len(paired), seeds=seeds))
    return folder
