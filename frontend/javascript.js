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

/** Show the available graph connections, including every cached flight reference. */
function showSearchResult(data, basicFilters) {
    const flightEdges = data.graph.edges.filter((edge) => edge.edge_type === "flight_connection");
    const carEdges = data.graph.edges.filter((edge) => edge.mode === "car");
    const regionalTrainEdges = data.graph.edges.filter((edge) => edge.edge_type === "train_connection" && edge.metadata.ticket_coverage === "Deutschlandticket");
    const nodeNames = new Map(data.graph.nodes.map((node) => [node.id, node.name]));
    const commercialTrainWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "commercial_trains");
    const flightWarning = (data.warnings || []).find((warning) => warning.infrastructure_type === "flights");
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
    filterNotice.textContent = `Filters selected: ${basicFilters.travellers} traveller(s); the traveller ${ticketStatus}; maximum ${data.graph.policy.max_transport_categories} transport categories. Repeated train legs count as one category.${transferRule}`;
    card.append(title, summary, filterNotice);

    const flightDetails = document.createElement("details");
    flightDetails.className = "connection_details";
    const flightSummary = document.createElement("summary");
    flightSummary.textContent = flightEdges.length
        ? `${flightEdges.length} cached flight reference(s) found`
        : "No cached flight reference was found for this date";
    flightDetails.appendChild(flightSummary);
    if (flightEdges.length) {
        const flights = document.createElement("div");
        flights.className = "flight_routes";
        flightEdges.forEach((edge) => {
            const flightCard = document.createElement("details");
            flightCard.className = "flight_route_card";
            const flightSummary = document.createElement("summary");
            const flight = edge.metadata.flight_number || "Flight number unavailable";
            flightSummary.textContent = `${edge.metadata.origin_iata} → ${edge.metadata.destination_iata} · ${flight} · €${edge.estimated_price_eur} reference price`;
            const details = document.createElement("div");
            details.className = "flight_route_details";
            const durationLabel = edge.metadata.duration_status === "Travelpayouts itinerary duration"
                ? `${edge.estimated_duration_minutes} min`
                : `${edge.estimated_duration_minutes} min estimated`;
            const fields = [
                ["Airline code", edge.metadata.airline || "Unavailable"],
                ["Flight number", flight],
                ["Departure", formatFlightDeparture(edge.metadata.departure_at)],
                ["Flight duration", durationLabel],
                ["Stops", Number.isInteger(edge.metadata.transfers) ? edge.metadata.transfers : "Unavailable"],
                ["Distance", `${edge.estimated_distance_km} km`],
                ["Price status", "Cached reference — confirm before booking"],
            ];
            fields.forEach(([label, value]) => {
                const field = document.createElement("p");
                const labelElement = document.createElement("strong");
                labelElement.textContent = `${label}: `;
                field.append(labelElement, value);
                details.appendChild(field);
            });
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
    } else {
        const explanation = document.createElement("p");
        explanation.textContent = flightWarning
            ? flightWarning.message
            : "Travelpayouts may not have a cached price for every airport pair and date. Car connections are still shown in the graph.";
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
            stats.textContent = `${departure} → ${arrival} · ${route.transfer_count} transfer(s) · ${route.total_estimated_distance_km} km`;
            const segments = document.createElement("ol");
            route.segments.forEach((segment) => {
                const item = document.createElement("li");
                item.textContent = `${describeRouteSegment(segment, nodeNames)} · ${formatDuration(segment.estimated_duration_minutes)} · ${formatEuro(segment.estimated_price_eur)}`;
                segments.appendChild(item);
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
    };
    if (!origin || !destination || !date || !departureTime) return showMessage("Enter origin, destination, date, and departure time before searching.", true);
    searchButton.disabled = true;
    searchButton.textContent = "Building graph…";
    try {
        const response = await fetch("http://localhost:8000/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ origin, destination, date, departure_time: departureTime, has_deutschlandticket: basicFilters.hasDeutschlandticket, travellers: basicFilters.travellers, max_transport_categories: basicFilters.maxTransportCategories, minimum_train_transfer_minutes: basicFilters.minimumTrainTransferMinutes }) });
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
