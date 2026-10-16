<?php

namespace ExternalSearch;

interface SearchDriver
{
	/**
	 * @return array{hits: Hit[], total: int}
	 * @throws \RuntimeException when the engine is unreachable, slow or answers garbage
	 */
	public function search(Query $q, int $offset, int $limit): array;
}
