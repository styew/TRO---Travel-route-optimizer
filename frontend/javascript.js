const searchButton = document.getElementById("search_button");

async function geocode(city) {

    const response = await fetch(
        `http://localhost:8000/geocode?city=${encodeURIComponent(city)}`
    );

    const data = await response.json();

    return data;
}


searchButton.addEventListener("click", async () => {


    const origin = document.getElementById("origin").value;
    const destination = document.getElementById("destination").value;

    console.log("origin:", origin, ", destination:", destination);

    const originData = await geocode(origin);
    const destinationData = await geocode(destination);

    console.log("Origin data:", originData);
    console.log("Destination data:", destinationData);

});