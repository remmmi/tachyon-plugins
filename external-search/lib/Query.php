<?php

namespace ExternalSearch;

/** A parsed search request. */
final class Query
{
	/** @var string Free text sent to the search engine (operators removed). */
	public string $text = '';
	/** @var array{from: ?string, to: ?string, before: ?int, after: ?int, has_attachment: bool} */
	public array $filters = ['from' => null, 'to' => null, 'before' => null, 'after' => null, 'has_attachment' => false];
	/** @var string 'relevance' (the engine's ranking) or 'date' (newest first, every word required). */
	public string $order = 'relevance';
	/** @var bool True when the request must be answered by the mail server itself (empty, or state operators). */
	public bool $nativeOnly = false;
}
