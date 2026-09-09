const searchButton = document.getElementById("search_button");

searchButton.addEventListener("click", async () => {

    console.log("1 - botão clicado");

    const origin = document.getElementById("origin").value;

    console.log("2 - origin:", origin);

    const response = await fetch(
        `http://localhost:8000/geocode?city=${encodeURIComponent(origin)}`
    );

    console.log("3 - response:", response);
    console.log("4 - status:", response.status);

    const data = await response.json();

    console.log("5 - data:", data);
});