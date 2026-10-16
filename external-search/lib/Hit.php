<?php

namespace ExternalSearch;

/** One search result: the message identity as indexed (folder, UIDVALIDITY at indexing time, UID). */
final class Hit
{
	public function __construct(public string $folder, public int $uidvalidity, public int $uid, public float $score) {}
}
