import { useEffect, useState } from "react";
import { api } from "./api";
import type { ConceptRef } from "./form";
import type { Concept, Domain } from "./types";

interface Props {
  label: string;
  domain: Domain;
  selected: ConceptRef[];
  onChange: (next: ConceptRef[]) => void;
  error?: string;
  errorId?: string; // the id of the error message, so the search box can point at it
}

/** Concept typeahead (GET /api/concepts): type at least two characters, pick results, remove chips. */
export function ConceptPicker({ label, domain, selected, onChange, error, errorId }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Concept[]>([]);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setResults([]);
      setFailed(false);
      return;
    }
    let current = true; // ignore answers to a query the user has already changed
    api
      .searchConcepts(q, domain)
      .then((found) => {
        if (!current) return;
        setResults(found);
        setFailed(false);
      })
      .catch(() => {
        if (!current) return;
        setResults([]);
        setFailed(true);
      });
    return () => {
      current = false;
    };
  }, [query, domain]);

  const pick = (concept: Concept) => {
    if (!selected.some((c) => c.id === concept.concept_id)) {
      onChange([...selected, { id: concept.concept_id, name: concept.name }]);
    }
    setQuery("");
  };

  return (
    <div className="picker">
      <label>
        {label}
        <input
          type="search"
          value={query}
          placeholder="Type at least 2 letters to search"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.preventDefault(); // Enter must not submit the whole cohort form
          }}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
        />
      </label>
      {failed && <p role="status">Concept search is unavailable right now.</p>}
      {results.length > 0 && (
        <ul aria-label={`${label} search results`}>
          {results.map((concept) => (
            <li key={concept.concept_id}>
              <button type="button" onClick={() => pick(concept)}>
                {concept.name} ({concept.concept_id})
              </button>
            </li>
          ))}
        </ul>
      )}
      {query.trim().length >= 2 && !failed && results.length === 0 && <p role="status">No matching concepts.</p>}
      {selected.length > 0 && (
        <ul aria-label={`${label} chosen`}>
          {selected.map((concept) => {
            const shown = concept.name ? `${concept.name} (${concept.id})` : `Concept ${concept.id}`;
            return (
              <li key={concept.id}>
                {shown}{" "}
                <button
                  type="button"
                  aria-label={`Remove ${shown}`}
                  onClick={() => onChange(selected.filter((c) => c.id !== concept.id))}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {error && <p id={errorId} role="alert">{error}</p>}
    </div>
  );
}
