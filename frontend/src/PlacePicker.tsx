import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { geocode } from "./api";
import type { Coordinate, GeocodeResult } from "./types";

export interface Place extends Coordinate {
  label: string;
}

interface PlacePickerProps {
  id: string;
  label: string;
  value: Place | null;
  onChange: (place: Place | null) => void;
}

export function PlacePicker({ id, label, value, onChange }: PlacePickerProps) {
  const [query, setQuery] = useState(value?.label ?? "");
  const [results, setResults] = useState<GeocodeResult[]>([]);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const [dismissed, setDismissed] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const listId = `${id}-places`;

  useEffect(() => {
    const normalized = query.trim();
    if (value || normalized.length < 3) {
      setResults([]);
      setStatus("");
      setLoading(false);
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setStatus("");
      try {
        const response = await geocode(normalized, controller.signal);
        if (controller.signal.aborted) return;
        const places = response.results.slice(0, 5);
        setResults(places);
        setStatus(places.length ? "" : "No places found");
        setActiveIndex(-1);
      } catch (error) {
        if (!controller.signal.aborted) {
          setResults([]);
          setStatus(error instanceof Error ? error.message : "Place search failed.");
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }, 300);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (abortRef.current === controller) abortRef.current = null;
    };
  }, [query, value]);

  function select(place: GeocodeResult) {
    abortRef.current?.abort();
    onChange({ label: place.label, lat: place.lat, lon: place.lon });
    setQuery(place.label);
    setResults([]);
    setStatus("");
    setActiveIndex(-1);
    setDismissed(true);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setDismissed(true);
      setResults([]);
      return;
    }
    if (!results.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setDismissed(false);
      setActiveIndex((index) => (index + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setDismissed(false);
      setActiveIndex((index) => (index <= 0 ? results.length - 1 : index - 1));
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      select(results[activeIndex]);
    }
  }

  const listOpen = !dismissed && (loading || results.length > 0 || Boolean(status));

  return (
    <div className="place-picker">
      <label className="field-label" htmlFor={id}>{label}</label>
      <input
        id={id}
        className="field-input place-picker__input"
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={listOpen}
        aria-controls={listId}
        aria-activedescendant={activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
        autoComplete="off"
        required
        value={query}
        onKeyDown={handleKeyDown}
        onChange={(event) => {
          setQuery(event.target.value);
          onChange(null);
          setDismissed(false);
        }}
      />
      {value && <p className="selected-place">Selected place · {value.label}</p>}
      {listOpen && (
        <ul className="place-picker__list" id={listId} role="listbox" aria-label={`${label} matches`}>
          {loading && <li className="place-picker__status" role="status">Searching places…</li>}
          {status && <li className="place-picker__status" role="status">{status}</li>}
          {results.map((place, index) => (
            <li key={`${place.lat},${place.lon},${place.label}`} role="presentation">
              <button
                className="place-picker__option"
                id={`${listId}-${index}`}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(place)}
              >
                {place.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
