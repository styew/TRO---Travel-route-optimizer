const searchButton = document.getElementById("search_button");

searchButton.addEventListener("click", async () => {

    const origin = document.getElementById("origin").value;

    console.log("Origin:", origin);

    const response = await fetch(
        `http://localhost:8000/geocode?city=${encodeURIComponent(origin)}`
    );

    const data = await response.json();

    console.log("API response:", data);
});