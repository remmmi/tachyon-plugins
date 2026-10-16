<?php

/**
 * EngineStatus against a fake HTTP transport: what the admin window says for each engine state, and which key
 * each request carries. Run by tools/test_plugins.sh.
 */

declare(strict_types=1);

require __DIR__ . '/../lib/Query.php';
require __DIR__ . '/../lib/Hit.php';
require __DIR__ . '/../lib/SearchDriver.php';
require __DIR__ . '/../lib/MeilisearchDriver.php';
require __DIR__ . '/../lib/EngineStatus.php';

use ExternalSearch\EngineStatus;

$ko = 0; $n = 0;
function ok(bool $c, string $m): void { global $ko, $n; $n++; if (!$c) { $ko++; echo "KO  $m\n"; } }

/** @param array<string, array{0:int,1:mixed}> $routes "METHOD path" => [status, body]; missing route = no answer */
function status(array $routes, string $statusKey = 'STATUS', string $embedder = '', ?array &$seen = null): array
{
	$seen = [];
	$transport = function (string $method, string $url, array $headers, string $body, int $timeout) use ($routes, &$seen): array {
		$path = substr($url, strlen('http://engine:7700'));
		$seen[] = [$method . ' ' . $path, $headers];
		if (!isset($routes[$method . ' ' . $path])) {
			throw new \RuntimeException('connection refused');
		}
		[$code, $data] = $routes[$method . ' ' . $path];
		return [$code, is_string($data) ? $data : json_encode($data)];
	};
	return (new EngineStatus('http://engine:7700/', 'SEARCH', $statusKey, 'messages', $embedder, 800, $transport))->lines();
}

$healthy = [
	'GET /health' => [200, ['status' => 'available']],
	'GET /indexes/messages/stats' => [200, ['numberOfDocuments' => 32145]],
	'GET /indexes/messages/settings' => [200, ['embedders' => ['mistral' => []], 'rankingRules' => ['sort', 'words']]],
	'POST /indexes/messages/search' => [200, ['hits' => [['date' => 1760572800]]]],
];

// engine down: one line, nothing else asked
$l = status([]);
ok(1 === count($l) && str_contains($l[0], 'not reachable at http://engine:7700'), 'engine down: one line naming the url');

// everything readable
$l = status($healthy, 'STATUS', 'mistral', $seen);
ok(str_contains(implode("\n", $l), 'Engine: reachable'), 'reachable');
ok(in_array('Index "messages": 32 145 documents.', $l, true), 'document count, thousands separated');
ok(in_array('Embedders on the index: mistral. Hybrid search uses "mistral".', $l, true), 'embedder found and in use');
ok(in_array('Ranking rule "sort" comes first: "Newest first" is a true date order.', $l, true), 'sort rule first');
ok(in_array('Newest indexed message: 2025-10-16 00:00 UTC.', $l, true), 'newest message date');
$keys = [];
foreach ($seen as [$req, $headers]) {
	$keys[$req] = implode(' ', array_filter($headers, fn($h) => str_starts_with($h, 'Authorization')));
}
ok('' === $keys['GET /health'], 'health asked without a key');
ok('Authorization: Bearer STATUS' === $keys['GET /indexes/messages/stats'] && 'Authorization: Bearer STATUS' === $keys['GET /indexes/messages/settings'],
	'stats and settings asked with the status key');
ok('Authorization: Bearer SEARCH' === $keys['POST /indexes/messages/search'], 'newest message asked with the search key');

// embedder set in the plugin but absent from the index; sort rule not first
$r = $healthy;
$r['GET /indexes/messages/settings'] = [200, ['embedders' => [], 'rankingRules' => ['words', 'sort']]];
$l = status($r, 'STATUS', 'mistral');
ok(in_array('Embedders on the index: none. Hybrid search is set to "mistral", which is NOT on the index: keyword results only.', $l, true),
	'missing embedder flagged');
ok(in_array('Ranking rule "sort" is not first: "Newest first" only breaks ties between equally relevant results.', $l, true),
	'sort rule not first flagged');

// no status key: no stats/settings request, said so
$l = status($healthy, '', '', $seen);
ok(in_array('Status key not set: document count, embedders and ranking rules are not shown.', $l, true), 'no status key stated');
ok([] === array_filter($seen, fn($s) => str_contains($s[0], '/stats') || str_contains($s[0], '/settings')), 'no status key: stats and settings not asked');

// status key refused, empty index, search key refused
$r = $healthy;
$r['GET /indexes/messages/stats'] = [403, ['message' => 'forbidden']];
unset($r['GET /indexes/messages/settings']);
$r['POST /indexes/messages/search'] = [200, ['hits' => []]];
$l = status($r);
ok(in_array('Index "messages": not readable with the status key (HTTP 403).', $l, true), 'status key refused');
ok(in_array('Newest indexed message: none (empty index).', $l, true), 'empty index');
$r['POST /indexes/messages/search'] = [401, ['message' => 'invalid key']];
ok(in_array('Search key: refused or index missing (HTTP 401).', status($r), true), 'search key refused');

echo $ko ? "EngineStatusTest: $ko KO / $n\n" : "EngineStatusTest: $n OK\n";
exit($ko ? 1 : 0);
