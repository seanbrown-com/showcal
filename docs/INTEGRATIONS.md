# Showcal integrations

## Concert discovery

Set TICKETMASTER_API_KEY for Ticketmaster Discovery concert/artist searches. Discovery is limited by ZIP radius and calendar month. Individually imported AXS events with venue coordinates join the same results. Automatic AXS discovery is not implemented. Ticket prices and inventory are not collected.

## Facebook

Provider-independent Facebook and web-search links are available. BRAVE_API_KEY optionally enables candidate Facebook event-page searches; the user confirms the event and RSVPs on Facebook.

## Jellyfin

Set `CREDENTIAL_KEY` before saving a connection in Settings. Enter either the Jellyfin username (for example, `showcal`) or the internal user ID, plus an API key/access token. This field is not a password. New connections require a token; an unchanged server can reuse a previously saved token by leaving the token field blank. Username lookup uses authenticated `/Users` and saves the uniquely matching user's internal ID. Direct IDs are verified with `/Users/{id}`. If the token cannot list users, enter the internal ID instead; it must still permit access to that user. Failed verification leaves the saved connection unchanged.

The connection stores the server base URL, resolved Jellyfin user ID, and an encrypted API token. Imports call `/Artists` and `/Artists/AlbumArtists` with `UserId`, `StartIndex`, `Limit`, and `Recursive`, passing `X-Emby-Token` as a header. The worker paginates both endpoints, deduplicates by case-folded name, and commits after every page has been fetched successfully. Existing favorites are preserved when removed from the library. No listening-history tracking is performed.

Use a dedicated read-scoped user token where available. A Jellyfin server API key can have broader permissions; keep app registration/access restricted to trusted people. The app intentionally permits private-network Jellyfin servers and is not designed as an open public proxy to arbitrary user-supplied servers.

https://typescript-sdk.jellyfin.org/interfaces/generated-client.ArtistApiGetArtistsRequest.html

## ZIP geocoding

US ZIP centroids come from `https://api.zippopotam.us/us/{zip}` and are cached in the database. Discovery requests use Ticketmaster's current `geoPoint` parameter, not the deprecated `latlong` parameter. A local Haversine calculation additionally excludes results beyond the requested miles. Events missing venue coordinates are excluded; pagination counts are from Ticketmaster before this validation. Search fails visibly if a new ZIP cannot be located. Distances are straight-line distances from a ZIP centroid and are approximate, not driving distances.

Coordinate attribution: [Zippopotam.us](https://www.zippopotam.us/) / [GeoNames](https://www.geonames.org/).

## Subscribed concert calendar

Open `/calendar/` (also linked from Settings) to create a private calendar subscription. It includes nearby shows by unsuppressed favorite artists from today through the same date six calendar months ahead. Month-end dates clamp to the last valid day. Known past/canceled events are excluded, and radius and favorite changes apply when the client next fetches the feed.

The worker refreshes discovery daily for enabled subscriptions, without sending calendar emails. Each refresh fetches all intersecting months (usually seven), bounded by the same 1,000-results-per-month provider limit as discovery. A failed refresh preserves the previous snapshot and reports the error on the calendar page. A feed with no successful initial refresh returns HTTP 503 with Retry-After, rather than an empty calendar that could remove entries. Imported AXS listings also appear; automatic AXS discovery and metadata refresh remain unavailable.

Set `APP_URL` to the address that calendar clients can reach. For deployment use the app's public HTTPS URL, configure `ALLOWED_HOSTS`/`CSRF_TRUSTED_ORIGINS`, and proxy requests to the web service. Localhost works only on the host computer. Google Calendar fetches subscriptions from its servers, so a LAN-only URL is insufficient. Calendar clients choose their own refresh schedules; the feed suggests 12 hours. Add the feed as a subscription, not a one-time import.

The feed URL contains a random UUID capability token: anyone who has it can read this calendar without logging in. Keep it private and avoid logging feed paths in a public access log. “Replace private link” invalidates the old URL (devices must resubscribe); “Disable subscription” deletes access. Neither action can erase copies already downloaded by a calendar client. Event UIDs stay stable across refreshes; timed events use UTC and date-only events use all-day dates. End times are estimates.
