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

/** Show the available graph connections, including every cached flight reference. */
function showSearchResult(data, basicFilters) {
    const flightEdges = data.graph.edges.filter((edge) => edge.edge_type === "flight_connection");
    const regionalTrainEdges = data.graph.edges.filter((edge) => edge.edge_type === "train_connection" && edge.metadata.ticket_coverage === "Deutschlandticket");
    const card = document.createElement("div");
    card.className = "result_card";
    const title = document.createElement("h2");
    title.textContent = `${data.origin.city_name} → ${data.destination.city_name}`;
    const summary = document.createElement("p");
    summary.textContent = `${data.graph.nodes.length} nodes and ${data.graph.edges.length} available connections.`;
    const filterNotice = document.createElement("p");
    const ticketStatus = basicFilters.hasDeutschlandticket ? "has a Deutschlandticket" : "does not have a Deutschlandticket";
    filterNotice.className = "filter_notice";
    filterNotice.textContent = `Filters selected: ${basicFilters.travellers} traveller(s); the traveller ${ticketStatus}; maximum ${data.graph.policy.max_transport_categories} transport categories. Repeated train legs count as one category.`;
    card.append(title, summary, filterNotice);

    const flightDetails = document.createElement("details");
    flightDetails.className = "connection_details";
    const flightSummary = document.createElement("summary");
    flightSummary.textContent = flightEdges.length
        ? `${flightEdges.length} cached flight reference(s) found`
        : "No cached flight reference was found for this date";
    flightDetails.appendChild(flightSummary);
    if (flightEdges.length) {
        const list = document.createElement("ul");
        flightEdges.forEach((edge) => {
            const item = document.createElement("li");
            const flight = edge.metadata.flight_number || "Flight number unavailable";
            item.textContent = `${edge.metadata.origin_iata} → ${edge.metadata.destination_iata} · ${flight} · €${edge.estimated_price_eur} cached reference price`;
            list.appendChild(item);
        });
        flightDetails.appendChild(list);
    } else {
        const explanation = document.createElement("p");
        explanation.textContent = "Travelpayouts may not have a cached price for every airport pair and date. Car connections are still shown in the graph.";
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
    if (basicFilters.hasDeutschlandticket) {
        const trainDetails = document.createElement("details");
        trainDetails.className = "connection_details";
        const trainSummary = document.createElement("summary");
        trainSummary.textContent = regionalTrainEdges.length
            ? `${regionalTrainEdges.length} Deutschlandticket regional route(s) found`
            : "No Deutschlandticket regional route was found";
        trainDetails.appendChild(trainSummary);
        if (regionalTrainEdges.length) {
            const list = document.createElement("ul");
            regionalTrainEdges.forEach((edge) => {
                const item = document.createElement("li");
                const lines = edge.metadata.legs.map((leg) => leg.line).filter(Boolean).join(" → ");
                item.textContent = `${lines || "Regional train"} · ${edge.estimated_duration_minutes} min · ${edge.metadata.transfer_count} transfer(s) · covered by Deutschlandticket`;
                list.appendChild(item);
            });
            trainDetails.appendChild(list);
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
    const basicFilters = {
        hasDeutschlandticket: document.getElementById("d_ticket").checked,
        travellers: Number(document.getElementById("travellers").value),
        maxTransportCategories: Number(document.getElementById("max_transport_categories").value),
    };
    if (!origin || !destination || !date) return showMessage("Enter origin, destination, and date before searching.", true);
    searchButton.disabled = true;
    searchButton.textContent = "Building graph…";
    try {
        const response = await fetch("http://localhost:8000/search", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ origin, destination, date, has_deutschlandticket: basicFilters.hasDeutschlandticket, travellers: basicFilters.travellers, max_transport_categories: basicFilters.maxTransportCategories }) });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || "The graph could not be created.");
        latestGraph = data.graph;
        showSearchResult(data, basicFilters);
    } catch (error) { showMessage(error.message, true); }
    finally { searchButton.disabled = false; searchButton.textContent = "Search routes"; }
});

document.getElementById("reset_graph_button").addEventListener("click", () => graphRenderer?.resetCamera());
