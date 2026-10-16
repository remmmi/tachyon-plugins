<?php

namespace ExternalSearch;

require_once __DIR__ . '/Query.php';

/**
 * Splits the raw search box content into free text, the operators this plugin handles
 * (from: to: before: after: has:attachment) and a "native only" verdict for everything
 * the mail server must keep answering (empty search, message-state operators, raw IMAP syntax).
 */
final class QueryParser
{
	/** Bare words that select on message state: the search engine knows nothing about them. */
	private const STATE_WORDS = ['unseen', 'seen', 'flagged', 'unflagged', 'answered', 'unanswered', 'deleted', 'undeleted'];
	/** Prefixes of state / scope operators handled by the mail server. */
	private const STATE_PREFIXES = ['is:', 'in:', 'keyword:', 'larger:', 'smaller:'];

	/**
	 * Upper-case IMAP search keywords of the core (MailSo\Imap\SearchCriterias::parseQueryString), used as FROM:x.
	 * Anything else upper-case ("RE: facture", "URGENT: x") is plain text. Mirrored in search.js (IMAP_KEYWORDS).
	 */
	private const IMAP_KEYWORDS = [
		'DATE', 'BODY', 'EMAIL', 'FROM', 'TO', 'SUBJECT', 'KEYWORD', 'IN',
		'SMALLER', 'LARGER', 'SINCE', 'ON', 'SENTON', 'SENTSINCE', 'SENTBEFORE', 'BEFORE',
		'OLDER', 'YOUNGER', 'HEADER', 'TO-ONLY', 'ATTACHMENT', 'FLAGGED', 'UNFLAGGED', 'SEEN',
		'UNSEEN', 'ANSWERED', 'UNANSWERED', 'DELETED', 'UNDELETED',
	];

	public static function parse(string $raw): Query
	{
		$q = new Query();
		$kept = [];
		foreach (self::tokenize($raw) as $tok) {
			if (self::isNativeToken($tok)) {
				$q->nativeOnly = true;
			}
			if (!self::applyOperator($q, $tok)) {
				$kept[] = $tok;
			}
		}
		$q->text = \implode(' ', $kept);
		if ('' === $q->text && $q->filters === (new Query())->filters) {
			$q->nativeOnly = true;
		}
		return $q;
	}

	/** Whitespace separated tokens; a double quote opens a span where spaces do not split and \" is an escaped quote. */
	private static function tokenize(string $raw): array
	{
		$tokens = [];
		$cur = '';
		$inQuote = false;
		$len = \strlen($raw);
		for ($i = 0; $i < $len; $i++) {
			$c = $raw[$i];
			if ($inQuote && '\\' === $c && $i + 1 < $len && '"' === $raw[$i + 1]) {
				$cur .= '\\"';
				$i++;
				continue;
			}
			if ('"' === $c) {
				$inQuote = !$inQuote;
			} else if (!$inQuote && \ctype_space($c)) {
				if ('' !== $cur) {
					$tokens[] = $cur;
					$cur = '';
				}
				continue;
			}
			$cur .= $c;
		}
		if ('' !== $cur) {
			$tokens[] = $cur;
		}
		return $tokens;
	}

	private static function isNativeToken(string $tok): bool
	{
		if ('"' === $tok[0]) {
			return false;                                   // a quoted phrase is text, whatever it contains
		}
		$lower = \strtolower($tok);
		if (\in_array($lower, self::STATE_WORDS, true)) {
			return true;
		}
		foreach (self::STATE_PREFIXES as $p) {
			if (\str_starts_with($lower, $p)) {
				return true;
			}
		}
		$colon = \strpos($tok, ':');                        // raw IMAP syntax of the core: FROM:x SUBJECT:y
		return false !== $colon && \in_array(\substr($tok, 0, $colon), self::IMAP_KEYWORDS, true);
	}

	/** Consumes the token when it is one of our operators with a valid value. */
	private static function applyOperator(Query $q, string $tok): bool
	{
		if (!\preg_match('/^(from|to|before|after|has):(.+)$/s', $tok, $m)) {
			return false;
		}
		$val = self::unquote($m[2]);
		if (null === $val || '' === \trim($val)) {
			return false;
		}
		switch ($m[1]) {
			case 'from':
			case 'to':
				$q->filters[$m[1]] = $val;
				return true;
			case 'before':
			case 'after':
				$ts = self::dayToEpoch($val);
				if (null === $ts) {
					return false;
				}
				$q->filters[$m[1]] = $ts;
				return true;
			case 'has':
				if ('attachment' !== \strtolower($val) && 'attachments' !== \strtolower($val)) {
					return false;
				}
				$q->filters['has_attachment'] = true;
				return true;
		}
		return false;
	}

	/** "a b" -> a b ; a\"b -> a"b ; unterminated quote -> null (the token stays text). */
	private static function unquote(string $v): ?string
	{
		if ('"' !== $v[0]) {
			return $v;
		}
		if (\strlen($v) < 2 || '"' !== \substr($v, -1) || \str_ends_with($v, '\\"')) {
			return null;
		}
		return \str_replace('\\"', '"', \substr($v, 1, -1));
	}

	private static function dayToEpoch(string $v): ?int
	{
		if (!\preg_match('/^(\d{4})-(\d{2})-(\d{2})$/', $v, $m) || !\checkdate((int) $m[2], (int) $m[3], (int) $m[1])) {
			return null;
		}
		// midnight in PHP's default timezone (the server's local day), not UTC
		return \mktime(0, 0, 0, (int) $m[2], (int) $m[3], (int) $m[1]);
	}
}
