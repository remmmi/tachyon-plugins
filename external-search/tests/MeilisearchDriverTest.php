<?php

/**
 * Exercises the Meilisearch driver through an injected HTTP transport: no network, no engine.
 * Run by tools/test_plugins.sh.
 */

declare(strict_types=1);
require __DIR__ . '/../lib/Query.php';
require __DIR__ . '/../lib/QueryParser.php';
require __DIR__ . '/../lib/Hit.php';
require __DIR__ . '/../lib/SearchDriver.php';
require __DIR__ . '/../lib/MeilisearchDriver.php';
use ExternalSearch\{MeilisearchDriver, QueryParser};
date_default_timezone_set('Europe/Paris');   // dates are local midnights of PHP's default timezone
$ko = 0; $n = 0;
function ok(bool $c, string $m): void { global $ko, $n; $n++; if (!$c) { $ko++; echo "KO  $m\n"; } }

$calls = [];
$reply = [200, json_encode(['hits' => [
	['folder' => 'INBOX', 'uidvalidity' => 7, 'uid' => 12, '_rankingScore' => 0.9],
	['folder' => 'Sent', 'uidvalidity' => 3, 'uid' => 4, '_rankingScore' => 0.5],
], 'estimatedTotalHits' => 42])];
$mk = function (array|\Closure $r) use (&$calls) {
	return new MeilisearchDriver('http://m:7700/', 'KEY', 'messages', 800, function (string $m, string $u, array $h, string $b, int $t) use (&$calls, &$r) {
		$calls[] = compact('m', 'u', 'h', 'b', 't');
		return $r instanceof \Closure ? $r() : $r;
	});
};
$last = function () use (&$calls) { $c = end($calls); return [$c, json_decode($c['b'], true)]; };

$d = $mk($reply);
$r = $d->search(QueryParser::parse('invoice skis'), 50, 25);
[$c, $b] = $last();
ok('POST' === $c['m'] && 'http://m:7700/indexes/messages/search' === $c['u'],
	'POST on /indexes/<index>/search, trailing slash trimmed');
ok(in_array('Authorization: Bearer KEY', $c['h'], true) && in_array('Content-Type: application/json', $c['h'], true),
	'bearer + json headers');
ok(800 === $c['t'], 'timeout forwarded');
ok('invoice skis' === $b['q'] && 50 === $b['offset'] && 25 === $b['limit'], 'q, offset, limit');
ok(['folder', 'uidvalidity', 'uid'] === $b['attributesToRetrieve'] && true === $b['showRankingScore'],
	'retrieve + ranking score');
ok(!isset($b['filter']) && !isset($b['attributesToSearchOn']) && !isset($b['sort']), 'no filter/sort for plain text');
ok(2 === count($r['hits']) && 42 === $r['total'], 'hits and estimatedTotalHits');
ok('INBOX' === $r['hits'][0]->folder && 7 === $r['hits'][0]->uidvalidity && 12 === $r['hits'][0]->uid && 0.9 === $r['hits'][0]->score,
	'hit mapping');

// accented folder names stay UTF-8 (the core encodes them for IMAP)
$d2 = $mk([200, json_encode(['hits' => [['folder' => 'Bestätigungen', 'uidvalidity' => 1, 'uid' => 2]], 'estimatedTotalHits' => 1])]);
ok('Bestätigungen' === $d2->search(QueryParser::parse('bestatigung'), 0, 10)['hits'][0]->folder,
	'UTF-8 folder name passed through untouched');

// filters
$d->search(QueryParser::parse('x before:2024-03-01 after:2024-01-01 has:attachment'), 0, 10);
[, $b] = $last();
ok(['date < ' . mktime(0, 0, 0, 3, 1, 2024), 'date >= ' . mktime(0, 0, 0, 1, 1, 2024), 'has_attachments = true'] === $b['filter'],
	'date and attachment filters');

// from/to alone: restricted attribute
$d->search(QueryParser::parse('from:"Dupont SA"'), 0, 10);
[, $b] = $last();
ok('"Dupont SA"' === $b['q'] && ['from'] === $b['attributesToSearchOn'], 'from alone restricts to attribute from');
ok(['date:desc'] === $b['sort'], 'empty-ish text: newest first');
$d->search(QueryParser::parse('to:marie'), 0, 10);
[, $b] = $last();
ok('marie' === $b['q'] && ['to'] === $b['attributesToSearchOn'], 'to alone restricts to attribute to');
// from + text: extra terms, no restriction
$d->search(QueryParser::parse('from:smith invoice'), 0, 10);
[, $b] = $last();
ok('invoice smith' === $b['q'] && !isset($b['attributesToSearchOn']), 'from + text: best effort extra term');
$d->search(QueryParser::parse('from:a to:b'), 0, 10);
[, $b] = $last();
ok('a b' === $b['q'] && !isset($b['attributesToSearchOn']), 'from + to: no restriction');
// only filters: empty q, newest first
$d->search(QueryParser::parse('has:attachment'), 0, 10);
[, $b] = $last();
ok('' === $b['q'] && ['date:desc'] === $b['sort'] && ['has_attachments = true'] === $b['filter'], 'filter only');

// injection: user text never reaches the filter expression
$hostile = 'from:"a\"b" ) OR folder = "x" before:2024-01-01';
$d->search(QueryParser::parse($hostile), 0, 10);
[, $b] = $last();
ok(['date < ' . mktime(0, 0, 0, 1, 1, 2024)] === $b['filter'],
	'filter holds only server-built numeric/boolean expressions');
ok(str_contains($b['q'], 'a"b') || str_contains($b['q'], 'ab'), 'hostile value travels as plain q text (JSON-encoded)');
foreach ($b['filter'] as $f) { ok(1 === preg_match('/^(date (<|>=) \d+|has_attachments = true)$/', $f), "filter shape: $f"); }

// hybrid search: only when an embedder is configured and there is text to embed
$mkh = fn(?string $emb, int $pct) => new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, fn() => $reply, $emb, $pct);
$calls = [];
$mkh('mistral', 50)->search(QueryParser::parse('invoice skis'), 0, 10);
$calls = []; $h = $mkh('mistral', 50); $h->search(QueryParser::parse('invoice skis'), 0, 10);
ok(true, 'hybrid driver constructible');
$probe = function (MeilisearchDriver $d, string $q) { $r = new \ReflectionMethod($d, 'buildBody'); $r->setAccessible(true); return $r->invoke($d, QueryParser::parse($q), 0, 10); };
$b = $probe($mkh('mistral', 50), 'invoice skis');
ok(['embedder' => 'mistral', 'semanticRatio' => 0.5] === $b['hybrid'],
	'hybrid block with embedder and ratio from percent');
$b = $probe($mkh('mistral', 100), 'x');
ok(1.0 === $b['hybrid']['semanticRatio'], 'percent 100 -> ratio 1.0');
$b = $probe($mkh('mistral', 50), 'has:attachment');
ok(!isset($b['hybrid']), 'no text: no hybrid (nothing to embed)');
$b = $probe($mkh('mistral', 50), 'from:smith');
ok(!isset($b['hybrid']), 'from alone: keyword only on that attribute');
$b = $probe($mkh('', 50), 'invoice');
ok(!isset($b['hybrid']), 'empty embedder: plain keyword search');
$b = $probe($mkh(null, 50), 'invoice');
ok(!isset($b['hybrid']), 'null embedder: plain keyword search');
$b = $probe($mkh('mistral', 250), 'x');
ok(1.0 === $b['hybrid']['semanticRatio'], 'percent clamped to 100');

// semantic hits make the engine report the whole index as total: cap it (hybrid_max_results)
$sem = [200, json_encode(['hits' => [['folder' => 'A', 'uidvalidity' => 1, 'uid' => 2]], 'estimatedTotalHits' => 26499, 'semanticHitCount' => 45])];
$mkc = fn(int $cap) => new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, fn() => $sem, 'mistral', 50, $cap);
ok(200 === $mkc(200)->search(QueryParser::parse('x'), 0, 50)['total'],
	'semantic hits: total capped to hybrid_max_results');
ok(26499 === $mkc(0)->search(QueryParser::parse('x'), 0, 50)['total'], 'cap 0 = no cap');
$kw = [200, json_encode(['hits' => [['folder' => 'A', 'uidvalidity' => 1, 'uid' => 2]], 'estimatedTotalHits' => 2980, 'semanticHitCount' => 0])];
ok(2980 === (new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, fn() => $kw, 'mistral', 50, 200))->search(QueryParser::parse('x'), 0, 50)['total'],
	'no semantic hit: total untouched');

// hybrid request refused (embedder down, out of credit): one retry without the hybrid block, keywords still answer
$seq = []; $n2 = 0;
$flaky = new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, function (string $m, string $u, array $h, string $b, int $t) use (&$seq, &$n2, $kw) {
	$seq[] = json_decode($b, true); $n2++;
	return isset(end($seq)['hybrid']) ? [400, '{"code":"vector_embedding_error"}'] : $kw;
}, 'mistral', 50);
$r = $flaky->search(QueryParser::parse('invoice'), 0, 10);
ok(2 === $n2 && isset($seq[0]['hybrid']) && !isset($seq[1]['hybrid']) && 'invoice' === $seq[1]['q'],
	'hybrid refused: retried once as keyword search');
ok(1 === count($r['hits']) && 2980 === $r['total'], 'keyword answer returned after the retry');
ok(true === $flaky->degraded, 'driver reports the degraded answer');
$seq = []; $n2 = 0;
$dead = new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, function () use (&$n2) { $n2++; return [500, '']; }, 'mistral', 50);
try { $dead->search(QueryParser::parse('invoice'), 0, 10); ok(false, 'engine down must throw'); }
catch (\RuntimeException $e) { ok(2 === $n2, 'engine down: one retry then the exception (native fallback takes over)'); }
$n2 = 0;
$plain = new MeilisearchDriver('http://m:7700', 'KEY', 'messages', 800, function () use (&$n2) { $n2++; return [500, '']; });
try { $plain->search(QueryParser::parse('invoice'), 0, 10); } catch (\RuntimeException $e) {}
ok(1 === $n2 && false === $plain->degraded, 'no embedder: no retry');

// failures -> RuntimeException
$fails = [
	'401' => [401, '{"message":"x"}'], '500' => [500, ''], '403' => [403, '{}'],
	'invalid json' => [200, 'not json'], 'json scalar' => [200, '"x"'], 'no hits key' => [200, '{}'],
];
foreach ($fails as $name => $resp) {
	try { $mk($resp)->search(QueryParser::parse('x'), 0, 10); ok(false, "$name must throw"); }
	catch (\RuntimeException $e) { ok(true, "$name throws"); }
}
try { $mk(fn() => throw new \Exception('boom'))->search(QueryParser::parse('x'), 0, 10); ok(false, 'transport exception must throw'); }
catch (\RuntimeException $e) { ok(str_contains($e->getMessage(), 'boom'), 'transport exception wrapped'); }
try { $mk(fn() => throw new \RuntimeException('timeout'))->search(QueryParser::parse('x'), 0, 10); ok(false, 'timeout must throw'); }
catch (\RuntimeException $e) { ok(true, 'timeout throws'); }
// malformed hit rows are skipped, not fatal
$r = $mk([200, json_encode(['hits' => [['folder' => 'A', 'uidvalidity' => 1, 'uid' => 2], ['folder' => 'B'], 'junk'], 'estimatedTotalHits' => 3])])->search(QueryParser::parse('x'), 0, 10);
ok(1 === count($r['hits']) && 0.0 === $r['hits'][0]->score, 'malformed rows skipped');
// the key never leaks into exception messages
try { $mk([500, 'Bearer KEY'])->search(QueryParser::parse('x'), 0, 10); } catch (\RuntimeException $e) { ok(!str_contains($e->getMessage(), 'KEY'), 'no key in error'); }
// date order: newest first, every word required, never hybrid (a semantic search matches the whole index)
$qd = QueryParser::parse('invoice skis');
$qd->order = 'date';
$dh = $mkh('mistral', 50);
$rb = new \ReflectionMethod($dh, 'buildBody');
$rb->setAccessible(true);
$b = $rb->invoke($dh, $qd, 0, 10);
ok(['date:desc'] === $b['sort'] && 'all' === $b['matchingStrategy'] && !isset($b['hybrid']),
	'date order: sort by date, matchingStrategy all, no hybrid block');
$mk($reply)->search(QueryParser::parse('invoice skis'), 0, 10);
[, $b] = $last();
ok(!isset($b['sort']) && !isset($b['matchingStrategy']), 'relevance order: no sort, default matching strategy');

// a non-200 answer is a failure even when its body looks like a result
try {
	$mk([401, json_encode(['hits' => [], 'estimatedTotalHits' => 0])])->search(QueryParser::parse('invoice'), 0, 10);
	ok(false, 'HTTP 401 must throw');
} catch (\RuntimeException $e) {
	ok(str_contains($e->getMessage(), '401'), 'HTTP 401 with a well-formed body still throws');
}
// a double quote typed inside a from:/to: value cannot break out of the phrase sent to the engine
$d->search(QueryParser::parse('from:"Smith \\"Ltd\\"" invoice'), 0, 10);
[, $b] = $last();
ok('invoice "Smith Ltd"' === $b['q'], 'quotes inside a from: value are dropped: ' . $b['q']);
echo $ko ? "MeilisearchDriverTest: $ko KO / $n\n" : "MeilisearchDriverTest: $n OK\n";
exit($ko ? 1 : 0);
