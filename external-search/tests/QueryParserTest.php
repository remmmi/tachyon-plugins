<?php

/**
 * Exercises the query parser: pure functions, no Tachyon, no engine.
 * Run by tools/test_plugins.sh.
 */

declare(strict_types=1);
require __DIR__ . '/../lib/Query.php';
require __DIR__ . '/../lib/QueryParser.php';
use ExternalSearch\QueryParser;
date_default_timezone_set('Europe/Paris');   // dates are local midnights of PHP's default timezone
$ko = 0; $n = 0;
function ok(bool $c, string $m): void { global $ko, $n; $n++; if (!$c) { $ko++; echo "KO  $m\n"; } }
$none = ['from' => null, 'to' => null, 'before' => null, 'after' => null, 'has_attachment' => false];

$q = QueryParser::parse('invoice skis');
ok('invoice skis' === $q->text && $none === $q->filters && !$q->nativeOnly, 'plain words');
$q = QueryParser::parse('from:smith invoice');
ok('invoice' === $q->text && 'smith' === $q->filters['from'] && !$q->nativeOnly, 'from: operator');
$q = QueryParser::parse('from:"Dupont SA"');
ok('' === $q->text && 'Dupont SA' === $q->filters['from'] && !$q->nativeOnly, 'quoted operator value');
$q = QueryParser::parse('to:marie@example.org quote');
ok('marie@example.org' === $q->filters['to'] && 'quote' === $q->text, 'to: operator');
$q = QueryParser::parse('before:2024-03-01 after:2024-01-01 x');
ok(mktime(0, 0, 0, 3, 1, 2024) === $q->filters['before'] && mktime(0, 0, 0, 1, 1, 2024) === $q->filters['after'] && 'x' === $q->text,
	'dates as local-midnight epochs');
ok(1709247600 === QueryParser::parse('before:2024-03-01')->filters['before'],
	'before: = 2024-03-01 00:00 Europe/Paris (23:00 UTC the day before)');
date_default_timezone_set('UTC');
ok(1709251200 === QueryParser::parse('before:2024-03-01')->filters['before'], 'before: follows the default timezone');
date_default_timezone_set('Europe/Paris');
$q = QueryParser::parse('has:attachment quote');
ok($q->filters['has_attachment'] && 'quote' === $q->text, 'has:attachment');
$q = QueryParser::parse('has:attachments');
ok($q->filters['has_attachment'] && '' === $q->text && !$q->nativeOnly,
	'has:attachments alias, filter only is not native');
ok(QueryParser::parse('')->nativeOnly, 'empty is native');
ok(QueryParser::parse('   ')->nativeOnly, 'blank is native');
foreach (['is:unread', 'is:flagged x', 'unseen', 'UNSEEN', 'flagged', 'unflagged', 'seen', 'answered', 'unanswered', 'deleted', 'undeleted',
		  'in:subtree x', 'keyword:todo', 'larger:1M', 'smaller:10k', 'invoice unseen', 'FROM:x', 'SUBJECT:"a b"'] as $raw) {
	ok(QueryParser::parse($raw)->nativeOnly, "native-only: $raw");
}
foreach (['RE: invoice', 'TR: quote', 'URGENT: x', 'Fwd: a', 'from:x RE: y'] as $raw) {
	$q = QueryParser::parse($raw);
	ok(!$q->nativeOnly && ('from:x RE: y' !== $raw || 'x' === $q->filters['from']),
		"upper-case prefix that is no IMAP keyword goes to the engine: $raw");
}
foreach (['DATE:2024', 'BODY:x', 'TO-ONLY:a', 'UNDELETED:', 'HEADER:x'] as $raw) {
	ok(QueryParser::parse($raw)->nativeOnly, "IMAP keyword is native-only: $raw");
}
ok(!QueryParser::parse('seenabove')->nativeOnly, 'a word merely containing "seen" is not an operator');
foreach (["Größe", "it's", '"invoice skis"', 'a "b c" d'] as $raw) {
	$q = QueryParser::parse($raw);
	ok($raw === $q->text && !$q->nativeOnly, "text intact: $raw");
}
$q = QueryParser::parse('before:2024-13-45 x');
ok(null === $q->filters['before'] && 'before:2024-13-45 x' === $q->text, 'invalid date ignored, kept in text');
$q = QueryParser::parse('before:2024-02-30');
ok(null === $q->filters['before'], 'impossible day ignored');
$q = QueryParser::parse('from:"a\"b" z');
ok('a"b' === $q->filters['from'] && 'z' === $q->text, 'escaped quote inside operator value');
$q = QueryParser::parse('from:');
ok(null === $q->filters['from'] && 'from:' === $q->text, 'empty operator value stays text');
$q = QueryParser::parse('"unterminated phrase');
ok('"unterminated phrase' === $q->text && !$q->nativeOnly, 'unterminated quote tolerated');
// a quoted phrase is text even when it looks like a native operator
foreach (['"is:unread"', '"FROM:x"', '"unseen"'] as $raw) {
	$q = QueryParser::parse($raw);
	ok(!$q->nativeOnly && $raw === $q->text, "quoted, stays text for the engine: $raw");
}
// has: only knows attachment(s); anything else stays text
$q = QueryParser::parse('has:star report');
ok(false === $q->filters['has_attachment'] && 'has:star report' === $q->text, 'has:<unknown> is not a filter');
echo $ko ? "QueryParserTest: $ko KO / $n\n" : "QueryParserTest: $n OK\n";
exit($ko ? 1 : 0);
