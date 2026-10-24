"use strict";

const searchButton = document.getElementById("search_button");
const output = document.getElementById("output");
const graphSection = document.getElementById("graph_section");
const graphCanvas = document.getElementById("graph_canvas");
let graphRenderer;
let latestGraph;

/** Show search feedback without inserting untrusted API text as HTML. */
function showMessage(message, isError = false) {
    output.textContent = message;
    output.className = isError ? "error_state" : "result_card";
}

/** Format a provider timestamp in the journey's German local time. */
function formatJourneyTime(timestamp) {
    if (!timestamp) return "Time unavailable";
    const time = new Date(timestamp);
    if (Number.isNaN(time.getTime())) return "Time unavailable";
    return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/Berlin", hour: "2-digit", minute: "2-digit",
    }).format(time);
}

/** Avoid presenting midnight as a flight time when a cached fare contains only a date. */
function formatFlightDeparture(timestamp) {
    return typeof timestamp === "string" && timestamp.includes("T")
        ? formatJourneyTime(timestamp)
        : "Schedule unavailable in cached reference";
}

/** Present a stored flight reference timestamp without implying it is a live fare. */
function formatReferenceSavedAt(timestamp) {
    if (!timestamp) return "Unknown";
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return "Unknown";
    return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Europe/Berlin", day: "2-digit", month: "2-digit",
        year: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
}

/** Convert a route duration into a compact summary suitable for an itinerary header. */
function formatDuration(minutes) {
    const hours = Math.floor(minutes / 60);
    const remainingMinutes = minutes % 60;
    return hours ? `${hours}h ${remainingMinutes}m` : `${remainingMinutes} min`;
}

/** Format an estimated route price consistently without building HTML from API data. */
function formatEuro(value) {
    return new Intl.NumberFormat("en-GB", {
        style: "currency",
        currency: "EUR",
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(value);
}

/** Describe one graph segment in a human-readable complete-route itinerary. */
function describeRouteSegment(segment, nodeNames) {
    const originName = nodeNames.get(segment.origin_node_id) || "Origin";
    const destinationName = nodeNames.get(segment.destination_node_id) || "Destination";
    if (segment.edge_type === "direct_car") return `Car directly: ${originName} → ${destinationName}`;
    if (segment.edge_type === "car_access_to_station") return `Car to station: ${originName} → ${destinationName}`;
    if (segment.edge_type === "car_access_to_airport") return `Car to airport: ${originName} → ${destinationName}`;
    if (segment.mode === "train") {
        const lines = (segment.metadata.legs || []).map((leg) => leg.line).filter(Boolean).join(" → ");
        return `Train: ${lines || `${originName} → ${destinationName}`}`;
    }
    if (segment.mode === "flight") return `Flight: ${originName} → ${destinationName}`;
    return `${segment.mode}: ${originName} → ${destinationName}`;
}

/** Render a DB-style sequence of regional train legs and their scheduled changes. */
function createTrainTimeline(edge) {
    const timeline = document.createElement("div");
    timeline.className = "train_timeline";
    const legs = edge.metadata.legs || [];
    const transfers = edge.metadata.transfer_waits || [];
    legs.forEach((leg, index) => {
        const legCard = document.createElement("article");
        legCard.className = "train_leg_card";
        const line = document.createElement("strong");
        line.className = "train_line";
        line.textContent = leg.line || "Regional train";
        const route = document.createElement("span");
        route.className = "train_route";
        route.textContent = `${leg.from || "Origin"} → ${leg.to || "Destination"}`;
        const times = document.createElement("span");
        times.className = "train_times";
        times.textContent = `${formatJourneyTime(leg.scheduled_departure)} – ${formatJourneyTime(leg.scheduled_arrival)}`;
        legCard.append(line, route, times);
        if (leg.operator) {
            const operator = document.createElement("span");
            operator.className = "train_operator";
            operator.textContent = leg.operator;
            legCard.appendChild(operator);
        }
        timeline.appendChild(legCard);

        const transfer = transfers[index];
        if (transfer) {
            const transferCard = document.createElement("div");
            transferCard.className = "train_transfer_card";
            const heading = document.createElement("strong");
            const wait = transfer.wait_minutes === null ? "Transfer time unavailable" : `${transfer.wait_minutes} min transfer`;
            heading.textContent = `${wait} · ${transfer.at_station || "Transfer station"}`;
            const detail = document.createElement("span");
            detail.textContent = `${formatJourneyTime(transfer.scheduled_arrival)} arrival → ${formatJourneyTime(transfer.scheduled_departure)} departure`;
            transferCard.append(heading, detail);
            timeline.appendChild(transferCard);
        }
    });
    return timeline;
}

/** Render live flight segments and their exact airport connection waits. */
function createFlightTimeline(edge) {
    const timeline = document.createElement("div");
    timeline.className = "flight_timeline";
    const legs = edge.metadata.flight_legs || [];
    const transfers = edge.metadata.transfer_waits || [];
    legs.forEach((leg, index) => {
        const legCard = document.createElement("article");
        legCard.className = "flight_leg_card";
        const flight = document.createElement("strong");
        flight.className = "flight_number";
        flight.textContent = leg.flight_number || "Flight number unavailable";
        const route = document.createElement("span");
        route.className = "flight_route";
        route.textContent = `${leg.from || leg.from_iata || "Origin"} (${leg.from_iata || "—"}) → ${leg.to || leg.to_iata || "Destination"} (${leg.to_iata || "—"})`;
        const times = document.createElement("span");
        times.className = "flight_times";
        times.textContent = `${formatJourneyTime(leg.scheduled_departure)} – ${formatJourneyTime(leg.scheduled_arrival)} · ${Number.isInteger(leg.duration_minutes) ? formatDuration(leg.duration_minutes) : "Duration unavailable"}`;
        legCard.append(flight, route, times);
        if (leg.airline) {
            const airline = document.createElement("span");
            airline.className = "flight_airline";
            airline.textContent = leg.airline;
            legCard.appendChild(airline);
        }
        timeline.appendChild(legCard);

        const transfer = transfers[index];
        if (transfer) {
            const transferCard = document.createElement("div");
            transferCard.className = "flight_transfer_card";
            const heading = document.createElement("strong");
            const wait = Number.isInteger(transfer.wait_minutes)
                ? `${formatDuration(transfer.wait_minutes)} connection`
                : "Connection time unavailable";
            heading.textContent = `${wait} · ${transfer.at_airport || transfer.at_airport_iata || "Connection airport"} (${transfer.at_airport_iata || "—"})`;
            const detail = document.createElement("span");
            detail.textContent = `${formatJourneyTime(transfer.scheduled_arrival)} arrival → ${formatJourneyTime(transfer.scheduled_departure)} next departure`;
            transferCard.append(heading, detail);
            timeline.appendChild(transferCard);
        }
    });
    return timeline;
}

/** Show the available graph connections, separating published schedules from price references. */
function showSearchResult(data, basicFilters) {
    const flightEdges = data.graph.edges.filter((edge) => edge.edge_type === "flight_connection");
    const carEdges = data.graph.edges.filter((edge) => edge.mode === "car");
    const regionalTrainEdges = data.graph.edges.filter((edge) => edge.edge_type === "train_connection" && edge.metadata.ticket_coverage === "Deutschlandticket");
    const internationalTrainEdges = data.graph.edges.filter((edge) => edge.edge_type === "train_connection"
        && edge.metadata.ticket_coverage === "estimated paid international rail fare");
    const nodeNames = new Map(data.graph.nodes.map((node) => [node.id, node.name]));
    const commercialTrainWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "commercial_trains");
    const flightWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "flights");
    const airportGatewayWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "airport_gateways");
    const internationalRailWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "international_rail");
    const scheduledFlightEdges = flightEdges.filter((edge) => edge.metadata.schedule_source);
    const card = document.createElement("div");
    card.className = "result_card";
    const title = document.createElement("h2");
    title.textContent = `${data.origin.city_name} → ${data.destination.city_name}`;
    const summary = document.createElement("p");
    summary.textContent = `${data.graph.nodes.length} nodes and ${data.graph.edges.length} available connections.`;
    const filterNotice = document.createElement("p");
    const ticketStatus = basicFilters.hasDeutschlandticket ? "has a Deutschlandticket" : "does not have a Deutschlandticket";
    const transferRule = basicFilters.minimumTrainTransferMinutes
        ? ` Train changes require at least ${basicFilters.minimumTrainTransferMinutes} minutes.`
        : "";
    filterNotice.className = "filter_notice";
    const timeRule = basicFilters.respectDepartureTime
        ? ` Connections must be reachable after the selected departure time; car → flight includes a ${data.graph.policy.airport_arrival_buffer_minutes}-minute airport buffer.`
        : " Best available options are compared across the selected day; the departure time does not remove a connection.";
    filterNotice.textContent = `Filters selected: ${basicFilters.travellers} traveller(s); the traveller ${ticketStatus}; maximum ${data.graph.policy.max_transport_categories} transport categories. Repeated train legs count as one category.${transferRule}${timeRule}`;
    card.append(title, summary, filterNotice);

    const flightDetails = document.createElement("details");
    flightDetails.className = "connection_details";
    const flightSummary = document.createElement("summary");
    flightSummary.textContent = flightEdges.length
        ? `${scheduledFlightEdges.length} published schedule(s) and ${flightEdges.length - scheduledFlightEdges.length} flight reference(s) found`
        : "No published flight schedule or cached flight reference was found for this date";
    flightDetails.appendChild(flightSummary);
    if (flightEdges.length) {
        const flights = document.createElement("div");
        flights.className = "flight_routes";
        flightEdges.forEach((edge) => {
            const flightCard = document.createElement("details");
            flightCard.className = "flight_route_card";
            const flightSummary = document.createElement("summary");
            const flight = edge.metadata.flight_number || "Flight number unavailable";
            const isPublishedSchedule = Boolean(edge.metadata.schedule_source);
            const priceLabel = edge.metadata.price_status === "historic_estimate"
                ? "historic estimate"
                : edge.metadata.price_status === "model_estimate"
                    ? "route estimate"
                    : "reference price";
            flightSummary.textContent = `${edge.metadata.origin_iata} → ${edge.metadata.destination_iata} · ${flight} · ${formatEuro(edge.estimated_price_eur)} ${priceLabel}${isPublishedSchedule ? " · published schedule" : ""}`;
            const details = document.createElement("div");
            details.className = "flight_route_details";
            const durationLabel = edge.metadata.duration_status === "Travelpayouts itinerary duration"
                ? `${edge.estimated_duration_minutes} min`
                : `${edge.estimated_duration_minutes} min estimated`;
            const isStoredReference = edge.metadata.price_status === "stored_reference_fallback";
            const hasDetailedItinerary = Array.isArray(edge.metadata.flight_legs)
                && edge.metadata.flight_legs.length > 0;
            const durationWarning = edge.metadata.duration_status === "Provider duration rejected as inconsistent with zero stops"
                ? `The provider reported ${edge.metadata.provider_duration_minutes} minutes with zero stops. TRO rejected that inconsistent duration and shows a geographic estimate instead.`
                : null;
            const fields = [
                ["Airline", edge.metadata.airline || "Unavailable"],
                ["Flight number", flight],
                ["Departure", formatFlightDeparture(edge.metadata.planned_departure || edge.metadata.departure_at)],
                [isPublishedSchedule ? "Scheduled arrival" : "Estimated itinerary arrival", edge.metadata.planned_arrival
                    ? formatJourneyTime(edge.metadata.planned_arrival)
                    : "Unavailable in cached reference"],
                ["Flight duration", durationLabel],
                ...(durationWarning ? [["Duration check", durationWarning]] : []),
                ["Stops", Number.isInteger(edge.metadata.transfers) ? edge.metadata.transfers : "Unavailable"],
                ["Connection detail", hasDetailedItinerary
                    ? "Exact segments and connection times are shown below."
                    : Number.isInteger(edge.metadata.transfers) && edge.metadata.transfers > 0
                        ? "The cached reference reports the number of stops, but not the layover airports or waiting times."
                        : "No layover details were supplied by this cached reference."],
                ["Distance", `${edge.estimated_distance_km} km`],
                ["Price status", isStoredReference
                    ? `Stored reference from ${formatReferenceSavedAt(edge.metadata.cache_fetched_at)} — confirm before booking`
                    : isPublishedSchedule
                        ? `${priceLabel}; the schedule does not confirm ticket availability.`
                        : "Cached provider reference — confirm before booking"],
                ...(isPublishedSchedule ? [["Schedule source", edge.metadata.schedule_source]] : []),
            ];
            fields.forEach(([label, value]) => {
                const field = document.createElement("p");
                const labelElement = document.createElement("strong");
                labelElement.textContent = `${label}: `;
                field.append(labelElement, value);
                details.appendChild(field);
            });
            if (hasDetailedItinerary) {
                details.appendChild(createFlightTimeline(edge));
            }
            if (edge.booking_url) {
                const bookingLink = document.createElement("a");
                bookingLink.className = "flight_booking_link";
                bookingLink.href = edge.booking_url;
                bookingLink.target = "_blank";
                bookingLink.rel = "noopener noreferrer";
                bookingLink.textContent = "Open flight search";
                details.appendChild(bookingLink);
            }
            flightCard.append(flightSummary, details);
            flights.appendChild(flightCard);
        });
        flightDetails.appendChild(flights);
        if (scheduledFlightEdges.length) {
            const attribution = document.createElement("p");
            attribution.className = "data_attribution";
            const label = document.createTextNode("Published flight schedule data powered by ");
            const link = document.createElement("a");
            link.href = "https://aerodatabox.com/";
            link.target = "_blank";
            link.rel = "noopener noreferrer";
            link.textContent = "AeroDataBox";
            attribution.append(label, link, document.createTextNode(". Prices are TRO estimates, not live fares."));
            flightDetails.appendChild(attribution);
        }
    } else {
        const explanation = document.createElement("p");
        explanation.textContent = [
            flightWarning?.message,
            airportGatewayWarning?.message,
            "Travelpayouts may not have a cached price for every airport pair and date. Car connections are still shown in the graph.",
        ].filter(Boolean).join(" ");
        flightDetails.appendChild(explanation);
    }
    const graphButton = document.createElement("button");
    graphButton.id = "view_graph_button";
    graphButton.type = "button";
    graphButton.textContent = "View 3D graph";
    graphButton.addEventListener("click", () => {
        graphSection.classList.remove("hidden");
        requestAnimationFrame(() => renderGraph(latestGraph));
        graphSection.scrollIntoView({ behavior: "smooth" });
    });
    card.append(flightDetails, graphButton);

    const completeRoutes = data.route_plan?.routes || [];
    if (completeRoutes.length) {
        const routeDetails = document.createElement("details");
        routeDetails.className = "connection_details complete_route_details";
        const routeSummary = document.createElement("summary");
        routeSummary.textContent = `${completeRoutes.length} complete route option(s) ranked by price`;
        routeDetails.appendChild(routeSummary);
        const routeList = document.createElement("div");
        routeList.className = "complete_routes";
        completeRoutes.forEach((route) => {
            const routeCard = document.createElement("details");
            routeCard.className = "complete_route_card";
            const optionSummary = document.createElement("summary");
            const modes = route.segments.map((segment) => segment.mode).join(" → ");
            optionSummary.textContent = `#${route.rank} · ${modes} · ${formatDuration(route.total_estimated_duration_minutes)} · ${formatEuro(route.total_estimated_price_eur)}`;
            const optionBody = document.createElement("div");
            optionBody.className = "complete_route_body";
            const stats = document.createElement("p");
            const departure = route.planned_departure ? formatJourneyTime(route.planned_departure) : "Not scheduled";
            const arrival = route.planned_arrival ? formatJourneyTime(route.planned_arrival) : "Not scheduled";
            const flightStops = route.segments.reduce(
                (total, segment) => total + (segment.mode === "flight" && Number.isInteger(segment.metadata.transfers)
                    ? segment.metadata.transfers : 0),
                0,
            );
            const trainChanges = route.segments.reduce(
                (total, segment) => total + (segment.mode === "train" && Number.isInteger(segment.metadata.transfer_count)
                    ? segment.metadata.transfer_count : 0),
                0,
            );
            const connectionLabels = [
                `${route.transport_category_changes} transport mode change(s)`,
                ...(flightStops ? [`${flightStops} flight stop(s)`] : []),
                ...(trainChanges ? [`${trainChanges} train change(s)`] : []),
            ];
            stats.textContent = `${departure} → ${arrival} · ${connectionLabels.join(" · ")} · ${route.total_estimated_distance_km} km`;
            const segments = document.createElement("ol");
            route.segments.forEach((segment, index) => {
                const item = document.createElement("li");
                const segmentTitle = document.createElement("strong");
                segmentTitle.textContent = `${describeRouteSegment(segment, nodeNames)} · ${formatDuration(segment.estimated_duration_minutes)} · ${formatEuro(segment.estimated_price_eur)}`;
                const segmentTiming = document.createElement("span");
                const segmentDeparture = segment.metadata.planned_departure;
                const segmentArrival = segment.metadata.planned_arrival;
                segmentTiming.textContent = segmentDeparture && segmentArrival
                    ? `${formatJourneyTime(segmentDeparture)} → ${formatJourneyTime(segmentArrival)}`
                    : "Schedule unavailable for this segment";
                item.append(segmentTitle, document.createElement("br"), segmentTiming);
                if (segment.mode === "flight" && Array.isArray(segment.metadata.flight_legs) && segment.metadata.flight_legs.length) {
                    item.appendChild(createFlightTimeline(segment));
                }
                // Every train segment includes its individual vehicles and waits,
                // so a D-Ticket access leg and its paid onward train read as one
                // understandable journey instead of two opaque graph edges.
                if (segment.mode === "train" && Array.isArray(segment.metadata.legs) && segment.metadata.legs.length) {
                    item.appendChild(createTrainTimeline(segment));
                }
                segments.appendChild(item);
                const nextSegment = route.segments[index + 1];
                if (segment.edge_type === "car_access_to_airport" && nextSegment?.mode === "flight") {
                    const bufferItem = document.createElement("li");
                    bufferItem.className = "connection_buffer";
                    const buffer = segment.metadata.airport_arrival_buffer_minutes || data.graph.policy.airport_arrival_buffer_minutes;
                    const flightDeparture = nextSegment.metadata.planned_departure
                        ? formatJourneyTime(nextSegment.metadata.planned_departure)
                        : "the flight departure";
                    bufferItem.textContent = `Airport arrival buffer · ${buffer} min for check-in and security before ${flightDeparture}`;
                    segments.appendChild(bufferItem);
                }
            });
            optionBody.append(stats, segments);
            routeCard.append(optionSummary, optionBody);
            routeList.appendChild(routeCard);
        });
        routeDetails.appendChild(routeList);
        card.insertBefore(routeDetails, graphButton);
    }

    if (carEdges.length) {
        const carDetails = document.createElement("details");
        carDetails.className = "connection_details";
        const carSummary = document.createElement("summary");
        carSummary.textContent = `${carEdges.length} estimated car connection(s) available`;
        carDetails.appendChild(carSummary);

        const routes = document.createElement("div");
        routes.className = "car_routes";
        carEdges.forEach((edge) => {
            const carCard = document.createElement("details");
            carCard.className = "car_route_card";
            const routeSummary = document.createElement("summary");
            const originName = nodeNames.get(edge.origin_node_id) || "Origin";
            const destinationName = nodeNames.get(edge.destination_node_id) || "Destination";
            const routeType = edge.edge_type === "direct_car"
                ? "Direct drive"
                : edge.edge_type === "car_access_to_airport"
                    ? "Drive to airport"
                    : "Drive to station";
            routeSummary.textContent = `${routeType} · ${formatDuration(edge.estimated_duration_minutes)} · ${formatEuro(edge.estimated_price_eur)}`;

            const details = document.createElement("div");
            details.className = "car_route_details";
            const departure = edge.metadata.planned_departure
                ? formatJourneyTime(edge.metadata.planned_departure)
                : "Departure time not selected";
            const arrival = edge.metadata.planned_arrival
                ? formatJourneyTime(edge.metadata.planned_arrival)
                : "Arrival estimate unavailable";
            const fields = [
                ["Route", `${originName} → ${destinationName}`],
                ["Estimated departure", departure],
                ["Estimated arrival", arrival],
                ["Driving time", formatDuration(edge.estimated_duration_minutes)],
                ["Road distance", `${edge.estimated_distance_km} km`],
                ["Estimated cost", formatEuro(edge.estimated_price_eur)],
                ["Cost basis", `${formatEuro(edge.metadata.estimated_car_cost_per_hour_eur)} per estimated driving hour`],
            ];
            fields.forEach(([label, value]) => {
                const field = document.createElement("p");
                const labelElement = document.createElement("strong");
                labelElement.textContent = `${label}: `;
                field.append(labelElement, value);
                details.appendChild(field);
            });
            const estimateNote = document.createElement("p");
            estimateNote.className = "car_estimate_note";
            estimateNote.textContent = "This is a geometric road estimate, not turn-by-turn navigation. Traffic, tolls, parking, fuel prices, and rental costs are not included.";
            details.appendChild(estimateNote);
            carCard.append(routeSummary, details);
            routes.appendChild(carCard);
        });
        carDetails.appendChild(routes);
        card.insertBefore(carDetails, graphButton);
    }
    if (basicFilters.hasDeutschlandticket) {
        const trainDetails = document.createElement("details");
        trainDetails.className = "connection_details";
        const trainSummary = document.createElement("summary");
        trainSummary.textContent = regionalTrainEdges.length
            ? `${regionalTrainEdges.length} Deutschlandticket regional route(s) found`
            : "No Deutschlandticket regional route was found";
        trainDetails.appendChild(trainSummary);
        if (regionalTrainEdges.length) {
            const routes = document.createElement("div");
            routes.className = "train_routes";
            regionalTrainEdges.forEach((edge) => {
                const routeCard = document.createElement("details");
                routeCard.className = "train_route_card";
                const routeSummary = document.createElement("summary");
                const lines = edge.metadata.legs.map((leg) => leg.line).filter(Boolean).join(" → ");
                const totalWait = edge.metadata.total_transfer_wait_minutes || 0;
                routeSummary.className = "train_route_summary";
                const timeRange = document.createElement("strong");
                timeRange.className = "journey_time_range";
                timeRange.textContent = `${formatJourneyTime(edge.metadata.planned_departure)} → ${formatJourneyTime(edge.metadata.planned_arrival)}`;
                const journeyStats = document.createElement("span");
                journeyStats.className = "journey_stats";
                journeyStats.textContent = `${formatDuration(edge.estimated_duration_minutes)} · ${edge.metadata.transfer_count} transfer(s)`;
                const trainPills = document.createElement("span");
                trainPills.className = "train_pills";
                edge.metadata.legs.map((leg) => leg.line).filter(Boolean).forEach((line) => {
                    const pill = document.createElement("span");
                    pill.className = "train_pill";
                    pill.textContent = line;
                    trainPills.appendChild(pill);
                });
                routeSummary.append(timeRange, journeyStats, trainPills);
                const itineraryInfo = document.createElement("p");
                itineraryInfo.className = "journey_detail_info";
                itineraryInfo.textContent = `${totalWait} min total waiting time · covered by Deutschlandticket · ${lines || "Regional train"}`;
                routeCard.append(routeSummary, itineraryInfo, createTrainTimeline(edge));
                routes.appendChild(routeCard);
            });
            trainDetails.appendChild(routes);
        } else {
            const explanation = document.createElement("p");
            const trainWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "regional_trains");
            explanation.textContent = trainWarning
                ? trainWarning.message
                : "No regional-only route was returned for this date.";
            trainDetails.appendChild(explanation);
        }
        card.insertBefore(trainDetails, graphButton);
    }
    if (internationalTrainEdges.length || internationalRailWarning) {
        const internationalDetails = document.createElement("details");
        internationalDetails.className = "connection_details";
        const internationalSummary = document.createElement("summary");
        internationalSummary.textContent = internationalTrainEdges.length
            ? `${internationalTrainEdges.length} estimated international rail continuation(s) found`
            : "No scheduled paid international rail continuation found";
        internationalDetails.appendChild(internationalSummary);
        if (internationalTrainEdges.length) {
            const routes = document.createElement("div");
            routes.className = "train_routes";
            internationalTrainEdges.forEach((edge) => {
                const routeCard = document.createElement("details");
                routeCard.className = "train_route_card";
                const routeSummary = document.createElement("summary");
                const lines = edge.metadata.legs.map((leg) => leg.line).filter(Boolean).join(" → ");
                routeSummary.textContent = `${formatJourneyTime(edge.metadata.planned_departure)} → ${formatJourneyTime(edge.metadata.planned_arrival)} · ${formatDuration(edge.estimated_duration_minutes)} · ${formatEuro(edge.estimated_price_eur)} estimated · ${lines || "International rail"}`;
                const note = document.createElement("p");
                note.className = "journey_detail_info";
                note.textContent = "Paid continuation after the Deutschlandticket boundary. Fare is a TRO estimate, not a ticket offer.";
                routeCard.append(routeSummary, note, createTrainTimeline(edge));
                routes.appendChild(routeCard);
            });
            internationalDetails.appendChild(routes);
        } else {
            const explanation = document.createElement("p");
            explanation.textContent = internationalRailWarning.message;
            internationalDetails.appendChild(explanation);
        }
        card.insertBefore(internationalDetails, graphButton);
    }
    if (commercialTrainWarning) {
        const commercialNotice = document.createElement("p");
        commercialNotice.className = "filter_notice";
        commercialNotice.textContent = commercialTrainWarning.message;
        card.insertBefore(commercialNotice, graphButton);
    }
    output.replaceChildren(card);
    output.className = "";
}

/** Render the serializable backend graph as an interactive geographic 3D scene. */
function renderGraph(graph) {
    if (!window.THREE || !window.THREE.OrbitControls) {
        graphCanvas.textContent = "The 3D library could not be loaded. Refresh with an internet connection.";
        return;
    }
    graphRenderer?.dispose();
    graphCanvas.replaceChildren();
    const width = graphCanvas.clientWidth;
    const height = 520;
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x07111f);
    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 2000);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    graphCanvas.appendChild(renderer.domElement);
    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const latitudes = graph.nodes.map((node) => node.latitude);
    const longitudes = graph.nodes.map((node) => node.longitude);
    const centerLat = (Math.min(...latitudes) + Math.max(...latitudes)) / 2;
    const centerLon = (Math.min(...longitudes) + Math.max(...longitudes)) / 2;
    const positions = new Map();
    const colors = { city_center: 0x4cc9f0, train_station: 0xf9c74f, airport: 0xf72585 };
    graph.nodes.forEach((node) => {
        const position = new THREE.Vector3((node.longitude - centerLon) * 18, node.type === "airport" ? 2 : 1, (node.latitude - centerLat) * -18);
        positions.set(node.id, position);
        const sphere = new THREE.Mesh(new THREE.SphereGeometry(node.type === "city_center" ? 0.44 : 0.32, 20, 20), new THREE.MeshStandardMaterial({ color: colors[node.type] }));
        sphere.position.copy(position);
        scene.add(sphere);
    });
    graph.edges.forEach((edge) => {
        const from = positions.get(edge.origin_node_id);
        const to = positions.get(edge.destination_node_id);
        const edgeColor = edge.mode === "flight" ? 0xf72585 : edge.mode === "train" ? 0x22c55e : 0x7dd3fc;
        if (from && to) scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints([from, to]), new THREE.LineBasicMaterial({ color: edgeColor })));
    });
    scene.add(new THREE.AmbientLight(0xffffff, 1.5));
    scene.add(new THREE.GridHelper(50, 20, 0x1d4ed8, 0x1e293b));
    const resetCamera = () => { camera.position.set(0, 14, 18); controls.target.set(0, 0, 0); controls.update(); };
    resetCamera();
    const animate = () => { controls.update(); renderer.render(scene, camera); renderer.userData.frame = requestAnimationFrame(animate); };
    animate();
    graphRenderer = { resetCamera, dispose: () => { cancelAnimationFrame(renderer.userData.frame); controls.dispose(); renderer.dispose(); } };
}

/** Fetch a route graph and unlock the visual graph button after success. */
searchButton.addEventListener("click", async () => {
    const origin = document.getElementById("origin").value.trim();
    const destination = document.getElementById("destination").value.trim();
    const date = document.getElementById("date").value;
    const departureTime = document.getElementById("departure_time").value;
    const basicFilters = {
        hasDeutschlandticket: document.getElementById("d_ticket").checked,
        travellers: Number(document.getElementById("travellers").value),
        maxTransportCategories: Number(document.getElementById("max_transport_categories").value),
        minimumTrainTransferMinutes: Number(document.getElementById("minimum_train_transfer_minutes").value),
        respectDepartureTime: document.getElementById("respect_departure_time").checked,
    };
    if (!origin || !destination || !date || !departureTime) return showMessage("Enter origin, destination, date, and departure time before searching.", true);
    searchButton.disabled = true;
    searchButton.textContent = "Building graph…";
    try {
        const apiBaseUrl = document.querySelector('meta[name="tro-api-base-url"]')?.content.replace(/\/$/, "") || "";
        const response = await fetch(`${apiBaseUrl}/search`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ origin, destination, date, departure_time: departureTime, respect_departure_time: basicFilters.respectDepartureTime, has_deutschlandticket: basicFilters.hasDeutschlandticket, travellers: basicFilters.travellers, max_transport_categories: basicFilters.maxTransportCategories, minimum_train_transfer_minutes: basicFilters.minimumTrainTransferMinutes }) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "The graph could not be created.");
        latestGraph = data.graph;
        showSearchResult(data, basicFilters);
    } catch (error) { showMessage(error.message, true); }
    finally { searchButton.disabled = false; searchButton.textContent = "Search routes"; }
});

document.getElementById("reset_graph_button").addEventListener("click", () => graphRenderer?.resetCamera());

/** Prevent accidental historical flight queries, which cannot have current cached prices. */
const dateInput = document.getElementById("date");
dateInput.min = new Date().toISOString().slice(0, 10);
if (!dateInput.value || dateInput.value < dateInput.min) {
    // The static page may have been opened on a later day than its original
    // default date. Move stale values forward so flight search is not silently
    // blocked by the backend before a traveller can make a valid search.
    dateInput.value = dateInput.min;
}
