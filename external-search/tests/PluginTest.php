<?php

/**
 * Exercises the plugin action (routing, fallback, page building) against minimal stand-ins of the
 * Tachyon and MailSo classes it touches. No Tachyon bootstrap, no search engine, no mail server.
 * Run by tools/test_plugins.sh.
 */

declare(strict_types=1);

namespace Tachyon {
	class Notifications
	{
		const CantGetMessageList = 201;
	}
}

namespace Tachyon\Exceptions {
	class ClientException extends \RuntimeException
	{
		public function __construct(int $iCode, ?\Throwable $oPrevious = null, string $sAdditionalMessage = '')
		{
			parent::__construct($sAdditionalMessage, $iCode, $oPrevious);
		}
	}
}

namespace MailSo\Mail {
	class MessageCollection implements \IteratorAggregate
	{
		public array $items = [];
		public int $totalEmails = 0;
		public string $FolderName = '';
		public int $Offset = 0;
		public int $Limit = 0;
		public string $Search = '';
		public string $SearchScope = '';
		public $FolderInfo = null;

		public function exchangeArray(array $a): void
		{
			$this->items = $a;
		}

		public function getIterator(): \Iterator
		{
			return new \ArrayIterator($this->items);
		}
	}

	class Message
	{
		public function __construct(private string $folder, private int $uid)
		{
		}

		public function Uid(): int
		{
			return $this->uid;
		}

		public function jsonSerialize(): array
		{
			return ['folder' => $this->folder, 'uid' => $this->uid];
		}
	}

	class MailClient
	{
		public array $fetched = [];
		/** "folder/uid" the mail server no longer has */
		public array $missing = [];

		// protected like the real one, and like the real one it returns the page folder by folder
		protected function MessageListMultiFolderFetch(MessageCollection $c, array $tuples): void
		{
			$this->fetched[] = $tuples;
			$grouped = [];
			foreach ($tuples as [$folder, $uid]) {
				$grouped[$folder][] = $uid;
			}
			$messages = [];
			foreach ($grouped as $folder => $uids) {
				foreach ($uids as $uid) {
					if (!\in_array("$folder/$uid", $this->missing, true)) {
						$messages[] = new Message((string) $folder, $uid);
					}
				}
			}
			$c->exchangeArray($messages);
		}
	}

	/** A core with the public entry point (Tachyon master since 5a3dbd9): order kept, missing pairs reported. */
	class MailClientWithTuples extends MailClient
	{
		public array $byTuples = [];

		public function MessageListByTuples(array $tuples, ?array &$missing = null): MessageCollection
		{
			$this->byTuples[] = $tuples;
			$c = new MessageCollection();
			$messages = [];
			$missing = [];
			foreach ($tuples as [$folder, $uid]) {
				if (\in_array("$folder/$uid", $this->missing, true)) {
					$missing[] = [$folder, $uid];
				} else {
					$messages[] = new Message((string) $folder, $uid);
				}
			}
			$c->exchangeArray($messages);
			return $c;
		}
	}
}

namespace Tachyon\Plugins {
	class FakeConfig
	{
		public array $v = [];

		public function Get(string $s, string $k, $d = null)
		{
			return $this->v[$k] ?? $d;
		}
	}

	class FakeImap
	{
		/** folder => current UIDVALIDITY; a folder absent from the map does not exist */
		public array $uv = [];
		public array $statusCalls = [];
		public array $selected = [];

		public function IsLoggined(): bool
		{
			return true;
		}

		public function FolderStatus(string $f)
		{
			$this->statusCalls[] = $f;
			if (!isset($this->uv[$f])) {
				throw new \RuntimeException('no such folder');
			}
			return (object) ['UIDVALIDITY' => $this->uv[$f]];
		}

		public function FolderStatusAndSelect(string $f)
		{
			$this->selected[] = $f;
			return (object) ['FullName' => $f];
		}
	}

	class FakeActions
	{
		public array $params = [];
		/** number of times the native MessageList was asked to answer */
		public int $native = 0;
		public \MailSo\Mail\MailClient $mc;
		public FakeImap $imap;

		public function __construct()
		{
			$this->mc = new \MailSo\Mail\MailClient();
			$this->imap = new FakeImap();
		}

		public function GetActionParam(string $k, $d = null)
		{
			return $this->params[$k] ?? $d;
		}

		public function DoMessageList(): array
		{
			$this->native++;
			return ['Result' => 'native'];
		}

		public function DefaultResponse($r, array $extra = [], string $a = ''): array
		{
			return \array_merge(['Action' => $a, 'Result' => $r], $extra);
		}

		public function getAccountFromToken()
		{
			return new \stdClass();
		}

		public function ImapClient()
		{
			return $this->imap;
		}

		public function MailClient()
		{
			return $this->mc;
		}
	}

	class FakeManager
	{
		public array $log = [];
		public FakeActions $a;

		public function __construct()
		{
			$this->a = new FakeActions();
		}

		public function WriteLog(string $s, int $t = \LOG_INFO): void
		{
			$this->log[] = [$t, $s];
		}

		public function Actions(): FakeActions
		{
			return $this->a;
		}
	}

	abstract class AbstractPlugin
	{
		public array $json = [];
		public array $js = [];
		public array $css = [];
		public FakeManager $m;
		public FakeConfig $c;

		public function addJsonHook(string $n, string $f = ''): self
		{
			$this->json[$n] = $f ?: $n;
			return $this;
		}

		public function addJs(string $f, bool $a = false): self
		{
			$this->js[] = $f;
			return $this;
		}

		public function addCss(string $f, bool $a = false): self
		{
			$this->css[] = $f;
			return $this;
		}

		public function UseLangs(?bool $b = null): bool
		{
			return true;
		}

		public function Manager(): FakeManager
		{
			return $this->m ??= new FakeManager();
		}

		public function Config(): FakeConfig
		{
			return $this->c ??= new FakeConfig();
		}

		public function jsonParam(string $k, $d = null)
		{
			return $this->Manager()->Actions()->GetActionParam($k, $d);
		}

		protected function configMapping(): array
		{
			return [];
		}

		public function mapping(): array
		{
			return $this->configMapping();
		}

		public function Init(): void
		{
		}
	}

	class Property
	{
		public string $name;

		public static function NewInstance(string $n): self
		{
			$p = new self();
			$p->name = $n;
			return $p;
		}

		public function __call($m, $a)
		{
			return $this;
		}
	}
}

namespace Tachyon\Enumerations {
	class PluginPropertyType
	{
		const STRING = 0, INT = 1, STRING_TEXT = 2, PASSWORD = 3, SELECTION = 4, BOOL = 5, URL = 6, SELECT = 8;
	}
}

namespace {
	require __DIR__ . '/../index.php';
	use ExternalSearch\{Hit, Query, SearchDriver};
	$ko = 0; $n = 0;
	function ok(bool $c, string $m): void { global $ko, $n; $n++; if (!$c) { $ko++; echo "KO  $m\n"; } }

	class FakeDriver implements SearchDriver {
		public array $calls = []; public $result;
		public function __construct($result) { $this->result = $result; }
		public function search(Query $q, int $offset, int $limit): array {
			$this->calls[] = [$q, $offset, $limit];
			if ($this->result instanceof \Throwable) { throw $this->result; }
			return $this->result;
		}
	}
	class TestPlugin extends ExternalSearchPlugin {
		public ?SearchDriver $drv = null;
		protected function driver(): ?SearchDriver { return $this->drv; }
	}
	$new = function (?SearchDriver $d, array $params, bool $fallback = true) {
		$p = new TestPlugin(); $p->Init(); $p->drv = $d; $p->Config()->v['fallback'] = $fallback;
		$p->Manager()->Actions()->params = $params;
		return $p;
	};
	$hit = fn(string $f, int $v, int $u) => new Hit($f, $v, $u, 1.0);

	// registration
	$p = new TestPlugin(); $p->Init();
	ok('DoExternalSearch' === ($p->json['ExternalSearch'] ?? ''), 'JSON action ExternalSearch registered');
	ok(!in_array('search.js', $p->js, true) || is_file(__DIR__ . '/../search.js'),
		'search.js registered only if it exists');
	$names = array_map(fn($x) => $x->name, $p->mapping());
	ok(['driver', 'url', 'api_key', 'status_key', 'index', 'hybrid_enabled', 'hybrid_embedder', 'semantic_percent', 'hybrid_max_results', 'default_order', 'show_order_switch', 'timeout_ms', 'fallback'] === $names,
		'config properties');

	// 1. native-only requests never touch the driver
	foreach (['', 'unseen', 'is:unread', 'flagged x', 'FROM:x'] as $s) {
		$d = new FakeDriver(['hits' => [], 'total' => 0]);
		$p = $new($d, ['folder' => 'INBOX', 'search' => $s, 'offset' => 0, 'limit' => 50]);
		$r = $p->DoExternalSearch();
		ok('native' === $r['Result'] && 1 === $p->Manager()->Actions()->native && [] === $d->calls && !isset($r['ExternalSearchFallback']),
			"native-only: '$s'");
	}
	// thread view and GET RawKey stay native
	$d = new FakeDriver(['hits' => [], 'total' => 0]);
	$p = $new($d, ['folder' => 'INBOX', 'search' => 'invoice', 'useThreads' => '1', 'threadUid' => '12']);
	$p->DoExternalSearch();
	ok(1 === $p->Manager()->Actions()->native && [] === $d->calls, 'thread view stays native');
	// threadUid 0 = no thread (what the client really sends): the engine answers
	$p = $new(new FakeDriver(['hits' => [], 'total' => 0]), ['folder' => 'INBOX', 'search' => 'invoice', 'useThreads' => '1', 'threadUid' => '0']);
	$p->DoExternalSearch();
	ok(0 === $p->Manager()->Actions()->native, 'threadUid 0 is not a thread view');
	$p = $new($d, ['RawKey' => 'abc', 'search' => 'invoice']);
	$p->DoExternalSearch();
	ok(1 === $p->Manager()->Actions()->native && [] === $d->calls, 'RawKey (GET) stays native');
	// not configured -> native, silently
	$p = $new(null, ['folder' => 'INBOX', 'search' => 'invoice']);
	$r = $p->DoExternalSearch();
	ok(1 === $p->Manager()->Actions()->native && !isset($r['ExternalSearchFallback']) && [] === $p->Manager()->log,
		'not configured: native, no marker, no log');

	// no API key, or an unknown driver: the real driver() builds nothing and the request goes native
	foreach ([[], ['api_key' => 'KEY', 'driver' => 'other']] as $conf) {
		$p = new ExternalSearchPlugin();
		$p->Init();
		$p->Config()->v = $conf;
		$p->Manager()->Actions()->params = ['folder' => 'INBOX', 'search' => 'invoice'];
		$r = $p->DoExternalSearch();
		ok(1 === $p->Manager()->Actions()->native && !isset($r['ExternalSearchFallback']) && [] === $p->Manager()->log,
			'unconfigured plugin (' . \json_encode($conf) . '): native, no marker, no log');
	}

	// 2. driver failure -> native + WARNING + marker
	$p = $new(new FakeDriver(new \RuntimeException('timeout')), ['folder' => 'INBOX', 'search' => 'invoice']);
	$r = $p->DoExternalSearch();
	ok(1 === $p->Manager()->Actions()->native && true === ($r['ExternalSearchFallback'] ?? null) && 'native' === $r['Result'],
		'driver throws: native fallback with marker');
	ok(1 === count($p->Manager()->log) && \LOG_WARNING === $p->Manager()->log[0][0] && str_contains($p->Manager()->log[0][1], 'external-search: fallback: timeout'),
		'fallback logged at WARNING');
	// fallback disabled -> ClientException
	$p = $new(new FakeDriver(new \RuntimeException('timeout')), ['folder' => 'INBOX', 'search' => 'invoice'], false);
	try { $p->DoExternalSearch(); ok(false, 'fallback off must throw'); }
	catch (\Tachyon\Exceptions\ClientException $e) { ok(201 === $e->getCode() && 0 === $p->Manager()->Actions()->native, 'fallback off: ClientException(CantGetMessageList), no native call'); }

	// 3. hits -> tuples in order, current UIDVALIDITY only
	$d = new FakeDriver(['hits' => [$hit('INBOX', 7, 12), $hit('Sent', 3, 4), $hit('INBOX', 7, 99), $hit('Old', 1, 5), $hit('Gone', 2, 6)], 'total' => 321]);
	$p = $new($d, ['folder' => 'INBOX', 'search' => 'from:smith invoice', 'offset' => '50', 'limit' => '25']);
	$a = $p->Manager()->Actions();
	$a->imap->uv = ['INBOX' => 7, 'Sent' => 3, 'Old' => 999];       // Old: UIDVALIDITY changed; Gone: folder missing
	$r = $p->DoExternalSearch();
	$c = $r['Result'];
	ok(1 === count($d->calls) && 50 === $d->calls[0][1] && 25 === $d->calls[0][2] && 'invoice' === $d->calls[0][0]->text,
		'driver gets the parsed query, offset and limit of the page');
	ok([['INBOX', 12], ['INBOX', 99], ['Sent', 4]] === ($a->mc->fetched[0] ?? null),
		'folder order (default): tuples grouped by folder, stale UIDVALIDITY and missing folder dropped');
	ok(['INBOX', 'Sent', 'Old', 'Gone'] === $a->imap->statusCalls, 'one status call per distinct folder');
	ok(321 === $c->totalEmails && 'INBOX' === $c->FolderName && 50 === $c->Offset && 25 === $c->Limit && 'from:smith invoice' === $c->Search && 'external' === $c->SearchScope,
		'collection fields');
	ok(is_object($c->FolderInfo) && ['INBOX'] === $a->imap->selected, 'FolderInfo = status of the current folder');
	ok(0 === $a->native && !isset($r['ExternalSearchFallback']), 'no native call, no marker');
	ok(2 === ($r['ExternalSearchSkipped'] ?? null), 'stale and unreachable hits counted as skipped rows');
	// a missing UID is simply absent: nothing to assert on the core side, the tuple list is what we control
	// zero usable hits: empty page, no fetch
	$d = new FakeDriver(['hits' => [$hit('Old', 1, 5)], 'total' => 1]);
	$p = $new($d, ['folder' => 'INBOX', 'search' => 'invoice']);
	$p->Manager()->Actions()->imap->uv = ['Old' => 2];
	$r = $p->DoExternalSearch();
	ok([] === $p->Manager()->Actions()->mc->fetched && [] === $r['Result']->items,
		'all stale: empty page, no fetch, no error');
	// 4. order of the page. The core returns it folder by folder; 'date' and 'relevance' put it back in engine order
	$page = fn() => new FakeDriver(['hits' => [$hit('INBOX', 7, 12), $hit('Sent', 3, 4), $hit('INBOX', 7, 99)], 'total' => 3]);
	$last = null;
	$run = function (array $params, array $conf = [], array $missing = [], bool $public = false) use ($new, $page, &$last) {
		$d = $page();
		$p = $new($d, ['folder' => 'INBOX', 'search' => 'invoice'] + $params);
		$p->Config()->v += $conf;
		$a = $p->Manager()->Actions();
		$public && $a->mc = new \MailSo\Mail\MailClientWithTuples();
		$a->imap->uv = ['INBOX' => 7, 'Sent' => 3];
		$a->mc->missing = $missing;
		$r = $p->DoExternalSearch();
		$last = [$p, $r];
		$c = $r['Result'];
		return [\array_map(fn($m) => $m->jsonSerialize()['folder'] . '/' . $m->Uid(), $c->items), $c->SearchScope, $d->calls[0][0]->order];
	};
	ok([['INBOX/12', 'INBOX/99', 'Sent/4'], 'external', 'relevance'] === $run([]),
		'default order: page grouped by folder as the core returns it, scope "external"');
	ok([['INBOX/12', 'Sent/4', 'INBOX/99'], 'external:date', 'date'] === $run(['externalOrder' => 'date']),
		'externalOrder=date: engine order restored, scope "external:date", driver asked for date');
	ok([['INBOX/12', 'Sent/4', 'INBOX/99'], 'external:relevance', 'relevance'] === $run(['externalOrder' => 'relevance']),
		'externalOrder=relevance: engine order restored, scope "external:relevance"');
	ok([['INBOX/12', 'Sent/4', 'INBOX/99'], 'external:date', 'date'] === $run([], ['default_order' => 'date']),
		'admin setting default_order=date applies without a request parameter');
	ok([['INBOX/12', 'Sent/4', 'INBOX/99'], 'external:relevance', 'relevance'] === $run(['externalOrder' => 'relevance'], ['default_order' => 'date']),
		'the request wins over the admin setting');
	ok([['INBOX/12', 'INBOX/99', 'Sent/4'], 'external', 'relevance'] === $run(['externalOrder' => 'size'], ['default_order' => 'bogus']),
		'unknown order values fall back to the folder grouping');
	ok([['INBOX/12', 'INBOX/99'], 'external:date', 'date'] === $run(['externalOrder' => 'date'], [], ['Sent/4']),
		'a message the server no longer has leaves no hole in the restored order');
	ok(1 === ($last[1]['ExternalSearchSkipped'] ?? null)
		&& [[\LOG_WARNING, 'external-search: indexed message not on the mail server: folder "Sent" uid 4']] === $last[0]->Manager()->log,
		'protected path: missing message counted and logged once');

	// 5. public entry point (MessageListByTuples): used instead of the protected method, same results
	ok([['INBOX/12', 'Sent/4', 'INBOX/99'], 'external:date', 'date'] === $run(['externalOrder' => 'date'], [], [], true),
		'public path: engine order as returned by the core');
	$mc = $last[0]->Manager()->Actions()->mc;
	ok([[['INBOX', 12], ['Sent', 4], ['INBOX', 99]]] === $mc->byTuples && [] === $mc->fetched,
		'public path: MessageListByTuples called with the tuples, protected method not touched');
	ok(!isset($last[1]['ExternalSearchSkipped']), 'full page: no skipped counter');
	ok([['INBOX/12', 'INBOX/99', 'Sent/4'], 'external', 'relevance'] === $run([], [], [], true),
		'public path, folder order: tuples grouped by folder before the call');
	ok([[['INBOX', 12], ['INBOX', 99], ['Sent', 4]]] === $last[0]->Manager()->Actions()->mc->byTuples,
		'public path, folder order: grouped tuples sent');
	ok([['INBOX/12', 'INBOX/99'], 'external:relevance', 'relevance'] === $run(['externalOrder' => 'relevance'], [], ['Sent/4'], true)
		&& 1 === ($last[1]['ExternalSearchSkipped'] ?? null) && 1 === \count($last[0]->Manager()->log),
		'public path: pairs reported missing by the core are counted and logged');

	// 6. hybrid switch: before 1.0 a name alone meant "on"
	$emb = function (array $conf) {
		$p = new ExternalSearchPlugin();
		$p->Config()->v = $conf;
		return (fn() => $this->hybridEmbedder())->call($p);
	};
	ok('mistral' === $emb(['hybrid_embedder' => 'mistral']), 'hybrid: configuration from before 1.0 (name only) stays on');
	ok('' === $emb(['hybrid_embedder' => 'mistral', 'hybrid_enabled' => false]), 'hybrid: switched off keeps the name but sends nothing');
	ok('mistral' === $emb(['hybrid_embedder' => ' mistral ', 'hybrid_enabled' => true]), 'hybrid: on, name trimmed');
	ok('' === $emb(['hybrid_enabled' => true]), 'hybrid: on without a name = keywords only');

	// 7. admin window text without a key: no engine call
	$p = new ExternalSearchPlugin();
	ok(str_contains($p->Description(), 'no API key set'), 'admin text: no key, native search stated');

	// limit clamp like the core
	$d = new FakeDriver(['hits' => [], 'total' => 0]);
	$p = $new($d, ['folder' => 'INBOX', 'search' => 'invoice', 'limit' => '5', 'offset' => '-3']);
	$p->DoExternalSearch();
	ok(0 === $d->calls[0][1] && 10 === $d->calls[0][2], 'offset and limit sanitized like MessageList');
	echo $ko ? "PluginTest: $ko KO / $n\n" : "PluginTest: $n OK\n";
	exit($ko ? 1 : 0);
}
