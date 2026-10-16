<?php
/**
 * external-search — answers the mail search box from an external full-text engine (Meilisearch) instead of
 * the slow IMAP SEARCH, and falls back to the native search whenever the engine is absent, slow or failing.
 *
 * Server side: JSON action "ExternalSearch" (client: PluginExternalSearch), same parameters as "MessageList".
 * The page of results is built by the core itself, so message objects, flags and threads render exactly like a
 * native list: MailClient::MessageListByTuples() where the core has it (public entry point, Tachyon master since
 * 5a3dbd9), otherwise MailClient::MessageListMultiFolderFetch() (protected, reached through a bound closure: best
 * effort, may break on any core upgrade).
 * License: AGPL-3.0-or-later (see LICENSE).
 */
require_once __DIR__ . '/lib/Query.php';
require_once __DIR__ . '/lib/QueryParser.php';
require_once __DIR__ . '/lib/Hit.php';
require_once __DIR__ . '/lib/SearchDriver.php';
require_once __DIR__ . '/lib/MeilisearchDriver.php';
require_once __DIR__ . '/lib/EngineStatus.php';

class ExternalSearchPlugin extends \Tachyon\Plugins\AbstractPlugin
{
	// VERSION: bump on every change of search.js (it is the cache key of the client bundle)
	const
		NAME     = 'External Search',
		AUTHOR   = 'remmmi',
		URL      = 'https://github.com/remmmi/tachyon-plugins',
		VERSION  = '1.0',
		RELEASE  = '2026-10-16',
		REQUIRED = '2.36.0',
		CATEGORY = 'Messages',
		LICENSE  = 'AGPL-3.0-or-later',
		DESCRIPTION = 'Full-text search of the message list through an external engine (Meilisearch),'
			. ' optionally hybrid (keywords + meaning), with automatic fallback to the native IMAP search.';

	public function Init(): void
	{
		$this->UseLangs(true);
		$this->addJsonHook('ExternalSearch', 'DoExternalSearch');
		if (\is_file(__DIR__ . '/search.js')) {
			$this->addJs('search.js');
			$this->addCss('search.css');
		}
		// optional: delete order-switch.js to keep the core's list as it is (results then stay grouped by folder
		// when the order is "folder", and come as one list of folder groups otherwise)
		if (\is_file(__DIR__ . '/order-switch.js')) {
			$this->addJs('order-switch.js');
		}
		$this->addCss('admin.css', true);
	}

	/**
	 * Text of the plugin window in the admin panel (the core calls this only there): the description, then a
	 * read-only report on the engine.
	 */
	public function Description(): string
	{
		$cfg = $this->Config();
		$text = static::DESCRIPTION;
		if ('' === (string) $cfg->Get('plugin', 'api_key', '')) {
			return $text . "\n\nEngine status: no API key set, every search uses the native IMAP search.";
		}
		$status = new \ExternalSearch\EngineStatus(
			(string) $cfg->Get('plugin', 'url', 'http://meilisearch:7700'),
			(string) $cfg->Get('plugin', 'api_key', ''),
			(string) $cfg->Get('plugin', 'status_key', ''),
			(string) $cfg->Get('plugin', 'index', 'messages'),
			$this->hybridEmbedder(),
			\max(500, (int) $cfg->Get('plugin', 'timeout_ms', 800))
		);
		return $text . "\n\nEngine status, checked when this window opened:\n- " . \implode("\n- ", $status->lines());
	}

	/** Name of the embedder to use, '' = keyword search only. */
	private function hybridEmbedder(): string
	{
		$cfg = $this->Config();
		$name = \trim((string) $cfg->Get('plugin', 'hybrid_embedder', ''));
		// absent from configurations written before 1.0: a name alone meant "on"
		$enabled = $cfg->Get('plugin', 'hybrid_enabled', null);
		return (null === $enabled ? '' !== $name : (bool) $enabled) ? $name : '';
	}

	protected function configMapping(): array
	{
		$T = \Tachyon\Enumerations\PluginPropertyType::class;
		return [
			\Tachyon\Plugins\Property::NewInstance('driver')->SetLabel('Search engine')
				->SetType($T::SELECT)->SetOptions([['id' => 'meilisearch', 'name' => 'Meilisearch']])
				->SetDefaultValue('meilisearch'),
			\Tachyon\Plugins\Property::NewInstance('url')->SetLabel('Engine URL')
				->SetType($T::STRING)->SetDefaultValue('http://meilisearch:7700')
				->SetDescription('Reachable from the webmail server only.'),
			\Tachyon\Plugins\Property::NewInstance('api_key')->SetLabel('API key')
				->SetType($T::PASSWORD)->SetDefaultValue('')
				->SetDescription('Use a search-only key. Empty = plugin inactive (native search).'),
			\Tachyon\Plugins\Property::NewInstance('status_key')->SetLabel('Status key (optional)')
				->SetType($T::PASSWORD)->SetDefaultValue('')
				->SetDescription('A key limited to the actions indexes.get, stats.get and settings.get. Lets the engine status above show the document count, the embedders and the ranking rules. Never a key that can write.'),
			\Tachyon\Plugins\Property::NewInstance('index')->SetLabel('Index name')
				->SetType($T::STRING)->SetDefaultValue('messages'),
			\Tachyon\Plugins\Property::NewInstance('hybrid_enabled')->SetLabel('Hybrid search (keywords + meaning)')
				->SetType($T::BOOL)->SetDefaultValue(false)
				->SetDescription('Needs an embedder already configured on the index; the deploy kit does that, not this window (it would take a key that can write). Costs one embedding call per search. See docs/hybrid.md.'),
			\Tachyon\Plugins\Property::NewInstance('hybrid_embedder')->SetLabel('Hybrid search: embedder name')
				->SetType($T::STRING)->SetDefaultValue('')
				->SetDescription('Name of the embedder on the index (Meilisearch settings.embedders). The engine status above lists the ones found.'),
			\Tachyon\Plugins\Property::NewInstance('semantic_percent')->SetLabel('Hybrid search: semantic weight (%)')
				->SetType($T::INT)->SetDefaultValue(50)
				->SetDescription('0 = keywords only, 100 = meaning only.'),
			\Tachyon\Plugins\Property::NewInstance('hybrid_max_results')->SetLabel('Hybrid search: maximum number of results')
				->SetType($T::INT)->SetDefaultValue(200)
				->SetDescription('Semantic search ranks every document, so the engine reports the whole index as total. 0 = no cap.'),
			\Tachyon\Plugins\Property::NewInstance('default_order')->SetLabel('Order of the results')
				->SetType($T::SELECT)->SetOptions([
					['id' => 'folder', 'name' => 'Grouped by folder'],
					['id' => 'date', 'name' => 'Newest first'],
					['id' => 'relevance', 'name' => 'Most relevant first'],
				])->SetDefaultValue('folder')
				->SetDescription('"Newest first" needs the "sort" ranking rule first on the index. A client may ask for date or relevance per request (externalOrder).'),
			\Tachyon\Plugins\Property::NewInstance('show_order_switch')->SetLabel('Show the order switch')
				->SetType($T::BOOL)->SetDefaultValue(true)->SetAllowedInJs(true)
				->SetDescription('Two buttons above the results, "Newest first" and "Most relevant first", remembered per browser. Results in engine order come as one list, each row tagged with its folder.'),
			\Tachyon\Plugins\Property::NewInstance('timeout_ms')->SetLabel('Timeout (ms)')
				->SetType($T::INT)->SetDefaultValue(800)
				->SetDescription('Past this delay the native search takes over.'),
			\Tachyon\Plugins\Property::NewInstance('fallback')->SetLabel('Fall back to native search on error')
				->SetType($T::BOOL)->SetDefaultValue(true),
		];
	}

	/** Null = plugin not configured (no key, unknown driver): every request goes native. */
	protected function driver(): ?\ExternalSearch\SearchDriver
	{
		$cfg = $this->Config();
		$key = (string) $cfg->Get('plugin', 'api_key', '');
		if ('' === $key || 'meilisearch' !== (string) $cfg->Get('plugin', 'driver', 'meilisearch')) {
			return null;
		}
		return new \ExternalSearch\MeilisearchDriver(
			(string) $cfg->Get('plugin', 'url', 'http://meilisearch:7700'),
			$key,
			(string) $cfg->Get('plugin', 'index', 'messages'),
			\max(100, (int) $cfg->Get('plugin', 'timeout_ms', 800)),
			null,
			$this->hybridEmbedder(),
			(int) $cfg->Get('plugin', 'semantic_percent', 50),
			\max(0, (int) $cfg->Get('plugin', 'hybrid_max_results', 200))
		);
	}

	/**
	 * Same request parameters as the core "MessageList" (folder, offset, limit, search, sort, uidNext, useThreads, threadUid…):
	 * ServiceActions::SetActionParams() stores the POST once for whichever action runs, and DoMessageList() reads it back
	 * through GetActionParam(), so the native fallback needs no copy of the parameters.
	 */
	public function DoExternalSearch(): array
	{
		$folder = (string) $this->jsonParam('folder', '');
		$raw = (string) $this->jsonParam('search', '');
		$query = \ExternalSearch\QueryParser::parse($raw);
		$driver = $this->driver();
		if ($query->nativeOnly || null === $driver || '' === $folder
			|| '' !== (string) $this->jsonParam('RawKey', '')          // GET variant of MessageList
			// inside one thread (the client always sends threadUid:0 outside): the engine knows nothing of threads
			|| (int) $this->jsonParam('threadUid', 0) > 0
		) {
			return $this->native(false);
		}

		try {
			return $this->searchPage($driver, $query, $folder, $raw);
		} catch (\Throwable $e) {
			$this->Manager()->WriteLog('external-search: fallback: ' . $e->getMessage(), \LOG_WARNING);
			if ((bool) $this->Config()->Get('plugin', 'fallback', true)) {
				return $this->native(true);
			}
			throw new \Tachyon\Exceptions\ClientException(\Tachyon\Notifications::CantGetMessageList, $e);
		}
	}

	/**
	 * 'date' or 'relevance': one flat list in the order of the engine. 'folder': the page as the core returns it,
	 * grouped by folder. The request (externalOrder) wins over the admin setting.
	 */
	private function order(): string
	{
		$asked = (string) $this->jsonParam('externalOrder', '');
		if (\in_array($asked, ['date', 'relevance'], true)) {
			return $asked;
		}
		$default = (string) $this->Config()->Get('plugin', 'default_order', 'folder');
		return \in_array($default, ['date', 'relevance'], true) ? $default : 'folder';
	}

	/**
	 * Fills the collection with the messages named by the tuples, in their order, through the core.
	 * @param array<int, array{0:string,1:int}> $tuples
	 * @return array<int, array{0:string,1:int}> the tuples no message came back for
	 */
	private function fetchPage($mailClient, \MailSo\Mail\MessageCollection $collection, array $tuples): array
	{
		if (\method_exists($mailClient, 'MessageListByTuples')) {
			$missing = [];
			$page = $mailClient->MessageListByTuples($tuples, $missing);
			$collection->exchangeArray(\iterator_to_array($page, false));
			return \is_array($missing) ? \array_values($missing) : [];
		}
		// Best effort, for cores without the public entry point (Tachyon 4.4.x and older): a protected method,
		// which a core upgrade may rename or change without notice. Then the native search takes over.
		if (!\method_exists($mailClient, 'MessageListMultiFolderFetch')) {
			throw new \RuntimeException('the core has neither MailClient::MessageListByTuples nor MessageListMultiFolderFetch');
		}
		\Closure::bind(
			fn($c, $t) => $this->MessageListMultiFolderFetch($c, $t),
			$mailClient,
			\MailSo\Mail\MailClient::class
		)($collection, $tuples);
		return $this->restoreOrder($collection, $tuples);
	}

	/**
	 * Older cores return a multi-folder page folder by folder: put it back in the order of the tuples.
	 * @param array<int, array{0:string,1:int}> $tuples
	 * @return array<int, array{0:string,1:int}> the tuples no message came back for
	 */
	private function restoreOrder(\MailSo\Mail\MessageCollection $collection, array $tuples): array
	{
		$rank = [];
		foreach ($tuples as $i => $t) {
			$rank[$t[0] . "\0" . $t[1]] = $i;
		}
		$sorted = [];
		foreach ($collection as $message) {
			$key = ($message->jsonSerialize()['folder'] ?? '') . "\0" . $message->Uid();
			if (isset($rank[$key])) {
				$sorted[$rank[$key]] = $message;
			}
		}
		\ksort($sorted);
		$collection->exchangeArray(\array_values($sorted));
		return \array_values(\array_diff_key($tuples, $sorted));
	}

	/**
	 * Tuples grouped by folder, folders in order of first appearance, engine order kept inside each folder: the
	 * page the client shows under folder headings.
	 * @param array<int, array{0:string,1:int}> $tuples
	 * @return array<int, array{0:string,1:int}>
	 */
	private static function groupByFolder(array $tuples): array
	{
		$grouped = [];
		foreach ($tuples as $t) {
			$grouped[$t[0]][] = $t;
		}
		return $grouped ? \array_merge(...\array_values($grouped)) : [];
	}

	private function native(bool $fallback): array
	{
		$res = $this->Manager()->Actions()->DoMessageList();
		if ($fallback) {
			$res['ExternalSearchFallback'] = true;
		}
		return $res;
	}

	private function searchPage(
		\ExternalSearch\SearchDriver $driver,
		\ExternalSearch\Query $query,
		string $folder,
		string $raw
	): array
	{
		$actions = $this->Manager()->Actions();
		// same bounds as MailClient::MessageList()
		$offset = \max(0, (int) $this->jsonParam('offset', 0));
		$limit = (int) $this->jsonParam('limit', 10);
		$limit = $limit < 10 ? 10 : ($limit > 999 ? 50 : $limit);

		$order = $this->order();
		$query->order = 'date' === $order ? 'date' : 'relevance';
		$found = $driver->search($query, $offset, $limit);
		if ($driver instanceof \ExternalSearch\MeilisearchDriver && $driver->degraded) {
			// the embedder refused the hybrid request (unreachable, out of credit…): keyword results were served instead
			$this->Manager()->WriteLog('external-search: hybrid search unavailable, keyword results served', \LOG_WARNING);
		}
		$imap = $this->imap();
		$stale = 0;
		$tuples = $this->currentTuples($imap, $found['hits'], $stale);
		if ('folder' === $order) {
			$tuples = self::groupByFolder($tuples);
		}

		$mailClient = $actions->MailClient();
		$collection = new \MailSo\Mail\MessageCollection();
		$collection->FolderName = $folder;
		$collection->Offset = $offset;
		$collection->Limit = $limit;
		$collection->Search = \trim($raw);
		// read by the client: any non-empty scope means "list spanning folders"; the suffix tells a flat list
		$collection->SearchScope = 'folder' === $order ? 'external' : 'external:' . $order;
		$collection->totalEmails = $found['total'];
		$missing = $tuples ? $this->fetchPage($mailClient, $collection, $tuples) : [];
		foreach ($missing as [$f, $u]) {
			// moved or deleted since it was indexed: the indexer's nightly reconciliation removes it from the index
			$this->Manager()->WriteLog('external-search: indexed message not on the mail server: folder "' . $f . '" uid ' . $u, \LOG_WARNING);
		}
		$collection->FolderInfo = $imap->FolderStatusAndSelect($folder);   // last: leaves the current folder selected
		$response = $actions->DefaultResponse($collection);
		if ($skipped = $stale + \count($missing)) {
			// the page is short by this many rows: the client may say so
			$response['ExternalSearchSkipped'] = $skipped;
		}
		return $response;
	}

	/**
	 * Drops hits whose UIDVALIDITY no longer matches the folder (folder recreated, UIDs renumbered) or whose folder is gone.
	 * One STATUS per distinct folder of the page.
	 * @param \ExternalSearch\Hit[] $hits
	 * @param int $stale set to the number of hits dropped
	 * @return array<int, array{0:string,1:int}>
	 */
	private function currentTuples($imap, array $hits, int &$stale = 0): array
	{
		$current = [];
		$tuples = [];
		foreach ($hits as $h) {
			if (!\array_key_exists($h->folder, $current)) {
				try {
					$current[$h->folder] = (int) $imap->FolderStatus($h->folder)->UIDVALIDITY;
				} catch (\Throwable $e) {
					$current[$h->folder] = null;
					$this->Manager()->WriteLog('external-search: folder "' . $h->folder . '" skipped: ' . $e->getMessage(), \LOG_WARNING);
				}
			}
			if (null !== $current[$h->folder] && $current[$h->folder] === $h->uidvalidity) {
				$tuples[] = [$h->folder, $h->uid];
			} else {
				$stale++;
			}
		}
		return $tuples;
	}

	/** IMAP connection of the current account (the core only opens it for its own actions; initMailClientConnection is protected). */
	private function imap()
	{
		$actions = $this->Manager()->Actions();
		$account = $actions->getAccountFromToken();
		if (!$actions->ImapClient()->IsLoggined()) {
			$account->ImapConnectAndLogin($this->Manager(), $actions->ImapClient(), \Tachyon\Api::Config());
		}
		return $actions->ImapClient();
	}
}
