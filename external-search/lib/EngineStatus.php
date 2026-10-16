<?php

namespace ExternalSearch;

/**
 * Read-only report on the search engine, shown in the plugin window of the admin panel.
 *
 * Without a status key: /health and the newest indexed message (a search, so the search-only key is enough).
 * With a status key (Meilisearch actions indexes.get, stats.get, settings.get): document count, embedders found
 * on the index and whether the "sort" ranking rule comes first. Nothing here writes to the engine.
 */
final class EngineStatus
{
	private \Closure $transport;

	/**
	 * @param \Closure|null $transport same contract as MeilisearchDriver's: fn(method, url, headers, body, timeoutMs):
	 *        array{0:int,1:string}. Default: curl.
	 */
	public function __construct(
		private string $url,
		private string $searchKey,
		private string $statusKey,
		private string $index,
		private string $hybridEmbedder,
		private int $timeoutMs,
		?\Closure $transport = null
	) {
		$this->url = \rtrim($url, '/');
		$this->transport = $transport ?? MeilisearchDriver::defaultTransport();
	}

	/** @return string[] one line per fact, in plain English */
	public function lines(): array
	{
		$out = [];
		[$code] = $this->call('GET', '/health', '');
		if (200 !== $code) {
			return ['Engine: not reachable at ' . $this->url . ($code ? ' (HTTP ' . $code . ')' : '') . '. Searches use the native IMAP search.'];
		}
		$out[] = 'Engine: reachable at ' . $this->url . '.';

		if ('' === $this->statusKey) {
			$out[] = 'Status key not set: document count, embedders and ranking rules are not shown.';
		} else {
			[$code, $stats] = $this->call('GET', '/indexes/' . \rawurlencode($this->index) . '/stats', $this->statusKey);
			if (200 === $code && \is_numeric($stats['numberOfDocuments'] ?? null)) {
				$out[] = 'Index "' . $this->index . '": ' . \number_format((int) $stats['numberOfDocuments'], 0, '.', ' ') . ' documents.';
			} else {
				$out[] = 'Index "' . $this->index . '": not readable with the status key' . ($code ? ' (HTTP ' . $code . ')' : '') . '.';
			}
			[$code, $settings] = $this->call('GET', '/indexes/' . \rawurlencode($this->index) . '/settings', $this->statusKey);
			if (200 === $code && \is_array($settings)) {
				$embedders = \array_keys(\is_array($settings['embedders'] ?? null) ? $settings['embedders'] : []);
				$line = 'Embedders on the index: ' . ($embedders ? \implode(', ', $embedders) : 'none') . '.';
				if ('' !== $this->hybridEmbedder) {
					$line .= \in_array($this->hybridEmbedder, $embedders, true)
						? ' Hybrid search uses "' . $this->hybridEmbedder . '".'
						: ' Hybrid search is set to "' . $this->hybridEmbedder . '", which is NOT on the index: keyword results only.';
				}
				$out[] = $line;
				$rules = \is_array($settings['rankingRules'] ?? null) ? $settings['rankingRules'] : [];
				$out[] = 'sort' === ($rules[0] ?? null)
					? 'Ranking rule "sort" comes first: "Newest first" is a true date order.'
					: 'Ranking rule "sort" is not first: "Newest first" only breaks ties between equally relevant results.';
			}
		}

		[$code, $newest] = $this->call('POST', '/indexes/' . \rawurlencode($this->index) . '/search', $this->searchKey,
			['q' => '', 'sort' => ['date:desc'], 'limit' => 1, 'attributesToRetrieve' => ['date']]);
		$date = $newest['hits'][0]['date'] ?? null;
		if (200 === $code && \is_numeric($date)) {
			$out[] = 'Newest indexed message: ' . \gmdate('Y-m-d H:i', (int) $date) . ' UTC.';
		} else if (200 === $code) {
			$out[] = 'Newest indexed message: none (empty index).';
		} else {
			$out[] = 'Search key: refused or index missing' . ($code ? ' (HTTP ' . $code . ')' : '') . '.';
		}
		return $out;
	}

	/** @return array{0:int,1:?array} HTTP status (0 = no answer) and decoded body */
	private function call(string $method, string $path, string $key, ?array $body = null): array
	{
		$headers = ['Content-Type: application/json'];
		'' === $key || $headers[] = 'Authorization: Bearer ' . $key;
		try {
			[$code, $raw] = ($this->transport)($method, $this->url . $path, $headers,
				null === $body ? '' : \json_encode($body, \JSON_THROW_ON_ERROR), $this->timeoutMs);
		} catch (\Throwable $e) {
			return [0, null];
		}
		$data = \json_decode((string) $raw, true);
		return [(int) $code, \is_array($data) ? $data : null];
	}
}
