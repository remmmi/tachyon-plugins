<?php

namespace ExternalSearch;

require_once __DIR__ . '/Query.php';
require_once __DIR__ . '/Hit.php';
require_once __DIR__ . '/SearchDriver.php';

final class MeilisearchDriver implements SearchDriver
{
	/** True when the last answer came from the keyword retry (the embedder refused the hybrid request). */
	public bool $degraded = false;

	private \Closure $transport;

	/**
	 * @param \Closure|null $transport fn(string $method, string $url, string[] $headers, string $body, int $timeoutMs):
	 *        array{0:int,1:string} (HTTP status, body). Default: curl.
	 * @param string|null $hybridEmbedder name of an embedder configured on the index; null/'' = keyword search only
	 * @param int $semanticPercent hybrid: 0 = keywords only … 100 = meaning only
	 * @param int $hybridMaxResults hybrid: semantic search ranks every document, so the engine reports the whole
	 *        index as total; cap it to keep pagination meaningful. 0 = no cap
	 */
	public function __construct(
		private string $url,
		private string $apiKey,
		private string $index,
		private int $timeoutMs,
		?\Closure $transport = null,
		private ?string $hybridEmbedder = null,
		private int $semanticPercent = 50,
		private int $hybridMaxResults = 200
	) {
		$this->url = \rtrim($url, '/');
		$this->transport = $transport ?? self::defaultTransport();
	}

	public function search(Query $q, int $offset, int $limit): array
	{
		$this->degraded = false;
		$body = $this->buildBody($q, $offset, $limit);
		try {
			$data = $this->request($body);
		} catch (\RuntimeException $e) {
			if (!isset($body['hybrid'])) {
				throw $e;
			}
			// the embedder is unreachable or out of credit: the keyword half of the search still works
			unset($body['hybrid']);
			$data = $this->request($body);
			$this->degraded = true;
		}
		$hits = [];
		foreach ($data['hits'] as $row) {
			if (!\is_array($row) || !\is_string($row['folder'] ?? null)
			 || !\is_numeric($row['uidvalidity'] ?? null) || !\is_numeric($row['uid'] ?? null)) {
				// a malformed row must not fail the whole page
				continue;
			}
			$hits[] = new Hit($row['folder'], (int) $row['uidvalidity'], (int) $row['uid'], (float) ($row['_rankingScore'] ?? 0.0));
		}
		$total = (int) ($data['estimatedTotalHits'] ?? $data['totalHits'] ?? \count($hits));
		if ($this->hybridMaxResults > 0 && (int) ($data['semanticHitCount'] ?? 0) > 0) {
			$total = \min($total, $this->hybridMaxResults);
		}
		return ['hits' => $hits, 'total' => $total];
	}

	/** @return array decoded engine answer (always has a 'hits' array) */
	private function request(array $body): array
	{
		try {
			[$status, $raw] = ($this->transport)(
				'POST',
				$this->url . '/indexes/' . \rawurlencode($this->index) . '/search',
				['Authorization: Bearer ' . $this->apiKey, 'Content-Type: application/json'],
				\json_encode($body, \JSON_UNESCAPED_UNICODE | \JSON_THROW_ON_ERROR),
				$this->timeoutMs
			);
		} catch (\Throwable $e) {
			throw new \RuntimeException('search request failed: ' . $e->getMessage(), 0, $e);
		}
		if (200 !== $status) {
			throw new \RuntimeException('search engine answered HTTP ' . $status);
		}
		$data = \json_decode((string) $raw, true);
		if (!\is_array($data) || !\is_array($data['hits'] ?? null)) {
			throw new \RuntimeException('search engine answered an unexpected payload');
		}
		return $data;
	}

	/**
	 * from:/to: cannot be an exact filter (the indexed fields are free text). Alone, the value is searched in that
	 * attribute only; next to other text it becomes one more term (best effort). Date and attachment filters are
	 * built from integers and constants only: no user string ever reaches the filter expression.
	 */
	private function buildBody(Query $q, int $offset, int $limit): array
	{
		$f = $q->filters;
		$text = $q->text;
		$onlyAttr = null;
		$extra = [];
		foreach (['from', 'to'] as $k) {
			if (null !== $f[$k]) {
				$extra[$k] = $f[$k];
			}
		}
		if ('' === $text && 1 === \count($extra)) {
			$onlyAttr = \array_key_first($extra);
		}
		$terms = [];
		foreach ($extra as $v) {
			$terms[] = \str_contains($v, ' ') ? '"' . \str_replace('"', '', $v) . '"' : \str_replace('"', '', $v);
		}
		$qs = \trim(\implode(' ', \array_filter([$text, ...$terms], fn($s) => '' !== $s)));

		$filter = [];
		if (null !== $f['before']) {
			$filter[] = 'date < ' . (int) $f['before'];
		}
		if (null !== $f['after']) {
			$filter[] = 'date >= ' . (int) $f['after'];
		}
		if ($f['has_attachment']) {
			$filter[] = 'has_attachments = true';
		}

		$body = [
			'q' => $qs,
			'offset' => $offset,
			'limit' => $limit,
			'attributesToRetrieve' => ['folder', 'uidvalidity', 'uid'],
			'showRankingScore' => true,
		];
		if ($filter) {
			$body['filter'] = $filter;
		}
		if (null !== $onlyAttr) {
			$body['attributesToSearchOn'] = [$onlyAttr];
		}
		if ('' === \trim($text)) {
			// nothing to rank by relevance: newest first
			$body['sort'] = ['date:desc'];
		} else if ('date' === $q->order) {
			// newest first. Needs the "sort" ranking rule first on the index, or the date only breaks ties.
			// Every word is required: sorted by date, partial matches would bury the full ones. No hybrid block:
			// a semantic search matches the whole index, which sorted by date is just the latest mail.
			$body['sort'] = ['date:desc'];
			$body['matchingStrategy'] = 'all';
		} else if (null !== $this->hybridEmbedder && '' !== $this->hybridEmbedder) {
			// free text: keyword + semantic results fused by the engine (the query is embedded server side)
			$body['hybrid'] = [
				'embedder' => $this->hybridEmbedder,
				'semanticRatio' => \max(0, \min(100, $this->semanticPercent)) / 100.0,
			];
		}
		return $body;
	}

	/** The curl transport, for other classes talking to the same engine (EngineStatus). */
	public static function defaultTransport(): \Closure
	{
		return \Closure::fromCallable([self::class, 'curlTransport']);
	}

	/** @return array{0:int,1:string} */
	private static function curlTransport(string $method, string $url, array $headers, string $body, int $timeoutMs): array
	{
		$ch = \curl_init($url);
		if (false === $ch) {
			throw new \RuntimeException('curl_init failed');
		}
		\curl_setopt_array($ch, [
			\CURLOPT_CUSTOMREQUEST => $method,
			\CURLOPT_HTTPHEADER => $headers,
			\CURLOPT_RETURNTRANSFER => true,
			\CURLOPT_CONNECTTIMEOUT_MS => \min($timeoutMs, 500),
			\CURLOPT_TIMEOUT_MS => $timeoutMs,
			\CURLOPT_FOLLOWLOCATION => false,
		]);
		'' === $body || \curl_setopt($ch, \CURLOPT_POSTFIELDS, $body);
		$out = \curl_exec($ch);
		if (false === $out) {
			$err = \curl_error($ch);
			\curl_close($ch);
			throw new \RuntimeException($err ?: 'curl error');
		}
		$code = (int) \curl_getinfo($ch, \CURLINFO_RESPONSE_CODE);
		\curl_close($ch);
		return [$code, (string) $out];
	}
}
